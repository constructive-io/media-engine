"""Where people laugh, found in the sound, since it is not in the words.

Asking a search for "funny" returns passages that talk about being funny.
Humour is not a subject, so what a passage means cannot find it. And a
recogniser is no help: it is trained to write speech down and treats
laughter as noise, so the clearest sign that a moment landed is thrown away
before anything else sees it. But laughter is a sound, and sounds can be
found.

Laughter is a quick train of voiced bursts. The syllables of speech come two
to four a second; the bursts of a laugh, four to eight. So how the loudness
rises and falls tells them apart: a laugh has more of its rise and fall above
four a second than below, and speech has less.

Three things are asked for together, because each alone has an innocent
explanation:

- it rises and falls quickly, as fast speech and a hand tapping a table do;
- it is voiced, as all speech is;
- it is loud for the recording, as a shout is.

All three at once, held for half a second, is laughter often enough to be
worth a person's time. That is all this claims: these are candidates, a
person confirms them in seconds, and what they confirm is exactly what a
trained model would need to learn from.

Each recording is judged against itself. Room tone, music and hiss all rise
and fall, and a fixed threshold cannot tell a lively recording from a noisy
one: a laugh is the exception within its own recording, and if everything in
a file looks like laughter, nothing in it is.

Needs numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from . import pcm

#: Samples a second the sound is read at.
RATE = 16000
#: Seconds between two readings of the loudness: a hundred a second.
_STEP_S = 0.010
#: Seconds listened to at once. Two give the rise and fall half a cycle a
#: second of detail, where one second left the band with five values in it.
_WINDOW_S = 2.0
#: The least sure a candidate may be, 0 to 1.
MIN_CONFIDENCE = 0.70
#: A laugh is short. Anything held longer is the recording: music, a fan.
MAX_SPAN_S = 8.0
#: The share of a recording's windows that may be candidates. One in fifty of
#: half an hour is still a candidate every half minute: a short list that is
#: mostly right is worth more than a long one that is mostly noise.
_RARE = 98.0
#: A recording shorter than this has too little to judge a laugh against.
MIN_LENGTH_S = 20.0
#: Where, on an asset, having listened is written down.
_NOTED = "laughter"


class LaughterError(LookupError):
    """A laugh that was asked about and cannot be told from the rest, or is not there."""


@dataclass(frozen=True)
class Laugh:
    start_s: float
    end_s: float
    #: 0 to 1.
    confidence: float
    #: The rate of its bursts, a second.
    bursts_hz: float


def _voiced(frames: Any, rate: int) -> Any:
    """The share of each frame's sound between 80 and 900 hertz, where a voice sits.

    A burst with nothing there is a door or a clap.
    """
    import numpy as np

    size = frames.shape[1]
    pitches = np.fft.rfftfreq(size, 1.0 / rate)
    voice = (pitches > 80) & (pitches < 900)
    shares = []
    for start in range(0, len(frames), 20000):      # a piece at a time: an hour is not held at once
        spectrum = np.abs(np.fft.rfft(frames[start:start + 20000] * np.hanning(size), axis=1))
        shares.append(spectrum[:, voice].sum(axis=1) / (spectrum.sum(axis=1) + 1e-12))
    return np.concatenate(shares)


def find(samples: Any, rate: int = RATE, *, min_confidence: float = MIN_CONFIDENCE,
         merge_gap_s: float = 0.4, max_span_s: float = MAX_SPAN_S) -> list[Laugh]:
    """The stretches of one channel of sound that are probably laughter, in order."""
    import numpy as np

    samples = np.asarray(samples, dtype=np.float64)
    size = int(rate * _STEP_S)
    if len(samples) < rate:
        return []
    frames = samples[:len(samples) // size * size].reshape(-1, size)
    loudness = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    voiced = _voiced(frames, rate)
    loud = float(np.percentile(loudness, 85))
    a_second = 1.0 / _STEP_S
    window = int(_WINDOW_S * a_second)
    rates = np.fft.rfftfreq(window, _STEP_S)
    fast, slow = (rates >= 4.0) & (rates <= 9.0), (rates >= 1.0) & (rates < 4.0)
    shape = np.hanning(window)

    heard = []      # (start, end, confidence, bursts a second, share that is quick)
    for at in range(0, max(0, len(loudness) - window), max(1, window // 4)):
        piece = loudness[at:at + window]
        if piece.mean() < loud * 0.5:
            continue                                # nothing is happening here
        moving = piece - piece.mean()
        if moving.std() < 1e-9:
            continue
        # The spectrum of the loudness, not of the sound. And a share, not a
        # peak: loudness moves less the faster the movement, so the strongest
        # value in any band is always at its slow edge and says nothing.
        spectrum = np.abs(np.fft.rfft(moving * shape))
        quick = float((spectrum[fast] ** 2).sum())
        quick = quick / (quick + float((spectrum[slow] ** 2).sum()) + 1e-12)
        # Half is as much quick movement as slow, which speech does not have.
        # It counts for most: it alone tells a laugh from any other loud voice.
        sure = (0.55 * min(1.0, max(0.0, (quick - 0.30) / 0.35))
                + 0.25 * min(1.0, max(0.0, (float(voiced[at:at + window].mean()) - 0.35) / 0.45))
                + 0.20 * min(1.0, float(piece.mean()) / (loud + 1e-12)))
        heard.append((at / a_second, (at + window) / a_second, sure,
                      float(rates[fast][int(np.argmax(spectrum[fast]))]), quick))
    if not heard:
        return []
    rare = float(np.percentile([quick for *_, quick in heard], _RARE))
    kept = [found for found in heard if found[4] >= rare and found[2] >= min_confidence]

    # Windows that touch are one laugh, up to a length. Its sureness is the
    # mean of theirs, not the most: a strong window joined to three weak ones
    # is a weaker claim, and taking the most made the longest spans the surest.
    joined: list[list[Any]] = []
    for start, end, sure, bursts, _quick in kept:
        if joined and start - joined[-1][1] <= merge_gap_s and end - joined[-1][0] <= max_span_s:
            joined[-1][1] = end
            joined[-1][2].append(sure)
        else:
            joined.append([start, end, [sure], bursts])
    laughs = []
    for start, end, sures, bursts in joined:
        sure = sum(sures) / len(sures)
        if end - start > 5.0:
            # A short burst is the shape of a laugh and a long one the shape
            # of a room. Tapered, not refused: a long laugh is kept, less sure.
            sure *= max(0.4, 1.0 - (end - start - 5.0) / 10.0)
        if sure >= min_confidence:
            laughs.append(Laugh(start, end, sure, bursts))
    return laughs


def listen(path: str | Path, **how: Any) -> list[Laugh]:
    """The laughter in a file."""
    return find(pcm.read(path, rate=RATE, stereo=False), RATE, **how)


def scan(project: str | None = None, match: str | None = None, *, min_confidence: float = MIN_CONFIDENCE,
         redo: bool = False) -> list[tuple[str, int]]:
    """Listen to every recording not yet listened to. Returns (file name, laughs found).

    That a recording was listened to is written on it, so one nobody laughs in
    is not listened to again. `redo` listens again to all of them and replaces
    what was found, keeping any a person has said yes or no to.
    """
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, a.path FROM media.assets a
            WHERE a.has_audio AND a.duration_s > %s
              AND (%s::text IS NULL OR a.project = %s) AND (%s = '' OR a.path ~* %s)
              AND (%s OR NOT (jsonb_exists(a.meta, %s)
                              OR EXISTS (SELECT 1 FROM media_video.laughter l WHERE l.asset_id = a.id)))
            ORDER BY a.duration_s, a.path""",
        (MIN_LENGTH_S, project, project, match or "", match or "", redo, _NOTED),
    )
    done = []
    for asset_id, path in cur.fetchall():
        if not Path(path).exists():
            continue
        laughs = listen(path, min_confidence=min_confidence)
        with db.transaction():
            unit = db.cursor()      # the cursor of this unit, so the file's laughs are stored together or not at all
            if redo:
                unit.execute("DELETE FROM media_video.laughter WHERE asset_id = %s AND confirmed IS NULL", (asset_id,))
            for laugh in laughs:
                unit.execute(
                    """INSERT INTO media_video.laughter (asset_id, start_s, end_s, confidence, modulation_hz)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (asset_id, start_s) DO UPDATE SET
                         end_s = EXCLUDED.end_s, confidence = EXCLUDED.confidence,
                         modulation_hz = EXCLUDED.modulation_hz
                       WHERE media_video.laughter.confirmed IS NULL""",
                    (asset_id, round(laugh.start_s, 2), round(laugh.end_s, 2), round(laugh.confidence, 4),
                     round(laugh.bursts_hz, 2)),
                )
            unit.execute("UPDATE media.assets SET meta = meta || %s WHERE id = %s",
                         (db.jsonb({_NOTED: {"found": len(laughs), "min_confidence": min_confidence}}), asset_id))
        done.append((Path(path).name, len(laughs)))
    return done


def found(project: str | None = None, *, limit: int | None = 25,
          ) -> list[tuple[Any, str, float, float, float, bool | None]]:
    """The surest laughs nobody has refused: (id, file name, start, end, confidence, what a person said)."""
    cur = db.cursor()
    cur.execute(
        """SELECT l.id, a.path, l.start_s, l.end_s, l.confidence, l.confirmed
             FROM media_video.laughter l JOIN media.assets a ON a.id = l.asset_id
            WHERE (%s::text IS NULL OR a.project = %s) AND l.confirmed IS DISTINCT FROM false
            ORDER BY l.confirmed IS TRUE DESC, l.confidence DESC, a.path, l.start_s LIMIT %s""",
        (project, project, limit),
    )
    return [(laugh_id, Path(path).name, float(start), float(end), float(sure), confirmed)
            for laugh_id, path, start, end, sure, confirmed in cur.fetchall()]


def rule(prefix: str, laughed: bool) -> tuple[str, float, float]:
    """Say that a candidate is laughter, or is not. Returns (file name, start, end).

    The candidate is named by the start of its id. One that fits two, or
    none, is an error: an answer recorded against a guess is worse than none.
    """
    bare = str(prefix).strip().lower().replace("-", "")
    if len(bare) < 4 or any(letter not in "0123456789abcdef" for letter in bare):
        raise LaughterError(f"{prefix!r} is not an id: give at least the first 4 characters of one")
    cur = db.cursor()
    cur.execute(
        """SELECT l.id, a.path, l.start_s, l.end_s
             FROM media_video.laughter l JOIN media.assets a ON a.id = l.asset_id
            WHERE replace(l.id::text, '-', '') LIKE %s LIMIT 2""",
        (bare + "%",),
    )
    fits = cur.fetchall()
    if not fits:
        raise LaughterError(f"no laugh's id starts with {prefix!r}; `media laughter` lists them")
    if len(fits) > 1:
        raise LaughterError(f"more than one laugh's id starts with {prefix!r}; give more of it")
    ((laugh_id, path, start, end),) = fits
    cur.execute("UPDATE media_video.laughter SET confirmed = %s WHERE id = %s", (laughed, laugh_id))
    return Path(path).name, float(start), float(end)
