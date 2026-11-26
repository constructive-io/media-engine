"""Shots, frames, colour signatures, object detection.

Four stages, cheapest first: find the shots, sample frames from each, describe
each shot's colour and motion, then look for objects in the sampled frames.
Each stage only runs on what the one before it kept, which is what makes hours
of footage affordable. Each is safe to run again: work already done is skipped.

`match` narrows any stage to assets whose path matches a regular expression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import db, ffmpeg
from ..detection import Detector, detector as default_detector
from ..settings import Settings
from . import cuts

#: Where in a shot to sample. The first and last frames of a shot are often
#: mid-transition and misrepresent it.
FRAME_POSITIONS = (0.5, 0.25, 0.75, 0.125, 0.875, 0.375, 0.625)
SIGNATURE_POSITIONS = (0.2, 0.4, 0.6, 0.8)
SIGNATURE_SIZE = (160, 90)
HUE_BINS = 24
VALUE_BINS = 8
#: Mean brightness (0 to 255) below which a shot is called black.
BLACK_BELOW = 8.0
#: The label a detector gives a person.
PERSON = "person"
DETECT_BATCH = 32


def _videos(cur: Any, match: str | None) -> list[tuple[Any, str, str, float | None]]:
    """Video assets as (id, original path, path to analyse, duration)."""
    cur.execute(
        """SELECT id, path, coalesce(proxy_path, path), duration_s
             FROM media.assets
            WHERE medium = 'video' AND (%s = '' OR path ~* %s)
            ORDER BY ingested_at, path""",
        (match or "", match or ""),
    )
    return cur.fetchall()


def shots(match: str | None = None, threshold: float = cuts.DEFAULT_THRESHOLD) -> list[tuple[str, int]]:
    """Split each video into shots. Returns (file name, shots found)."""
    cur = db.cursor()
    done = []
    for asset_id, path, source, duration in _videos(cur, match):
        cur.execute("SELECT count(*) FROM media_video.shots WHERE asset_id = %s", (asset_id,))
        if cur.fetchone()[0]:
            continue
        spans = cuts.shots(source, threshold=threshold)
        if not spans and duration:
            spans = [(0.0, duration)]  # unreadable frame by frame: one continuous take
        if not spans:
            continue
        cur.executemany(
            """INSERT INTO media_video.shots (asset_id, idx, start_s, end_s)
               VALUES (%s,%s,%s,%s) ON CONFLICT (asset_id, idx) DO NOTHING""",
            [(asset_id, index, start, end) for index, (start, end) in enumerate(spans)],
        )
        done.append((Path(path).name, len(spans)))
    return done


def frames(
    match: str | None = None, per_shot: int = 3, *, settings: Settings | None = None,
) -> list[tuple[str, int]]:
    """Save sample frames from each shot and measure them.

    Returns (file name, frames saved). Each shot takes the average sharpness
    and exposure of its frames.
    """
    from . import pixels

    settings = settings or Settings.from_env()
    cur = db.cursor()
    done = []
    for asset_id, path, source, _duration in _videos(cur, match):
        cur.execute(
            """SELECT s.id, s.idx, s.start_s, s.end_s FROM media_video.shots s
                WHERE s.asset_id = %s AND NOT EXISTS
                  (SELECT 1 FROM media_video.frames f WHERE f.shot_id = s.id)
                ORDER BY s.idx""",
            (asset_id,),
        )
        todo = cur.fetchall()
        described = ffmpeg.probe(source) if todo else None
        if not described or not described.width or not described.height:
            continue
        folder = settings.work_dir / "frames" / str(asset_id)[:8]
        saved = 0
        for shot_id, index, start, end in todo:
            measured = []
            for position in sorted(FRAME_POSITIONS[:max(1, per_shot)]):
                t_s = start + (end - start) * position
                still = folder / f"{index:05d}_{t_s:.2f}.jpg"
                raw = ffmpeg.frame(source, t_s, pix_fmt="gray", still=still)
                if len(raw) != described.width * described.height:
                    still.unlink(missing_ok=True)
                    continue
                gray = pixels.image(raw, described.width, described.height, channels=1)
                sharp, exposure, bright = pixels.sharpness(gray), pixels.clipped(gray), float(gray.mean())
                cur.execute(
                    """INSERT INTO media_video.frames
                         (shot_id, t_s, path, sharpness, exposure, brightness)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (shot_id, t_s) DO NOTHING""",
                    (shot_id, t_s, str(still), sharp, exposure, bright),
                )
                measured.append((sharp, exposure, bright))
                saved += 1
            if measured:
                sharp, exposure, bright = (sum(column) / len(measured) for column in zip(*measured))
                cur.execute(
                    """UPDATE media_video.shots
                          SET sharpness = %s, exposure = %s, is_black = %s WHERE id = %s""",
                    (sharp, exposure, bright < BLACK_BELOW, shot_id),
                )
        if saved:
            done.append((Path(path).name, saved))
    return done


def signatures(match: str | None = None) -> int:
    """Give each shot a colour and motion signature. Returns shots signed.

    Hue is weighted by saturation and brightness: a dark or grey pixel has a
    hue value but no colour, and should not vote.
    """
    import numpy as np

    from . import pixels

    cur = db.cursor()
    cur.execute(
        """SELECT s.id, s.start_s, s.end_s, coalesce(a.proxy_path, a.path)
             FROM media_video.shots s JOIN media.assets a ON a.id = s.asset_id
            WHERE a.medium = 'video' AND (%s = '' OR a.path ~* %s)
              AND NOT EXISTS (SELECT 1 FROM media_video.signatures g WHERE g.shot_id = s.id)
            ORDER BY a.path, s.idx""",
        (match or "", match or ""),
    )
    width, height = SIGNATURE_SIZE
    signed = 0
    for shot_id, start, end, source in cur.fetchall():
        span = max(0.01, end - start)
        hues, values, saturations, motion = [], [], [], []
        previous = None
        for position in SIGNATURE_POSITIONS:
            raw = ffmpeg.frame(source, start + span * position, size=SIGNATURE_SIZE)
            if len(raw) != width * height * 3:
                continue
            rgb = pixels.image(raw, width, height)
            hue, saturation, value = pixels.hsv(rgb)
            hues.append(np.histogram(hue, bins=HUE_BINS, range=(0, 360), weights=saturation * value)[0])
            values.append(np.histogram(value, bins=VALUE_BINS, range=(0, 1))[0])
            saturations.append(float(saturation.mean()) * 255.0)
            gray = pixels.luma(rgb)
            if previous is not None:
                motion.append(float(np.abs(gray - previous).mean()))
            previous = gray
        if not hues:
            continue
        hue_histogram = np.mean(hues, axis=0)
        value_histogram = np.mean(values, axis=0)
        hue_histogram = hue_histogram / (hue_histogram.sum() or 1.0)
        value_histogram = value_histogram / (value_histogram.sum() or 1.0)
        cur.execute(
            """INSERT INTO media_video.signatures
                 (shot_id, hue, value, sat_mean, motion, dominant_h)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (shot_id) DO NOTHING""",
            (shot_id, [float(x) for x in hue_histogram], [float(x) for x in value_histogram],
             float(np.mean(saturations)), float(np.mean(motion)) if motion else 0.0,
             float(np.argmax(hue_histogram)) * (360.0 / HUE_BINS)),
        )
        signed += 1
    return signed


def shot_size(area: float) -> str:
    """The framing a subject's share of the frame implies.

    The thresholds are a starting point, not a finding.
    """
    if area < 0.02:
        return "wide"
    return "medium" if area < 0.15 else "close"


def detect(
    match: str | None = None, confidence: float = 0.35, *, detector: Detector | None = None,
) -> int:
    """Find objects in the sampled frames. Returns frames looked at.

    Each shot then takes a subject count, the largest person's share of the
    frame, and the framing that implies, or `none` when nobody is in it. A
    shot that has its framing has been searched and is not searched again.
    """
    finder = detector or default_detector()
    cur = db.cursor()
    cur.execute(
        """SELECT f.id, f.path, f.shot_id FROM media_video.frames f
             JOIN media_video.shots s ON s.id = f.shot_id
             JOIN media.assets a ON a.id = s.asset_id
            WHERE (%s = '' OR a.path ~* %s) AND s.shot_size IS NULL
              AND NOT EXISTS (SELECT 1 FROM media_video.detections d WHERE d.frame_id = f.id)
            ORDER BY f.path""",
        (match or "", match or ""),
    )
    todo = [row for row in cur.fetchall() if row[1] and Path(row[1]).exists()]
    # In batches, so a long run keeps what it has found if it is interrupted.
    for start in range(0, len(todo), DETECT_BATCH):
        batch = todo[start:start + DETECT_BATCH]
        found = finder.detect([Path(row[1]) for row in batch], confidence)
        for (frame_id, _path, _shot_id), detections in zip(batch, found):
            if detections:
                cur.executemany(
                    """INSERT INTO media_video.detections
                         (frame_id, class_name, confidence, cx, cy, w, h, model)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(frame_id, d.label, d.confidence, d.cx, d.cy, d.w, d.h, finder.model)
                     for d in detections],
                )
    # Summed up from what is stored, not from this run, so a run that picks
    # up where an interrupted one stopped still sees every frame of a shot.
    for shot_id in dict.fromkeys(row[2] for row in todo):
        cur.execute(
            """SELECT count(*), max(d.w * d.h) FROM media_video.detections d
                 JOIN media_video.frames f ON f.id = d.frame_id
                WHERE f.shot_id = %s AND d.class_name = %s GROUP BY f.id""",
            (shot_id, PERSON),
        )
        per_frame = cur.fetchall()
        # Count people in the frame that shows the most of them, not across
        # frames: three frames of one person are one person.
        most = max((count for count, _ in per_frame), default=0)
        biggest = max((area for _, area in per_frame), default=None)
        cur.execute(
            """UPDATE media_video.shots
                  SET subject_n = %s, subject_area = %s, shot_size = %s WHERE id = %s""",
            (most, biggest, shot_size(biggest) if per_frame else "none", shot_id),
        )
    return len(todo)
