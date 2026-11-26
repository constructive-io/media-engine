"""What the tests share: a database to roll back, and media made on the spot.

The repository holds no media. Every picture and sound a test needs is made by
ffmpeg from its built-in test sources, in a temporary folder.

Tests that touch the database run only when `CONSTRUCTIVE_MEDIA_TEST_DATABASE_URL`
names a database with the schema modules deployed. Each runs in a transaction
that is rolled back, so they leave nothing behind.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import unittest
from pathlib import Path

from constructive_media import db, ffmpeg
from constructive_media.settings import Database, Settings

TEST_DATABASE_VARIABLE = "CONSTRUCTIVE_MEDIA_TEST_DATABASE_URL"

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None

needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
needs_numpy = unittest.skipUnless(HAVE_NUMPY, "numpy is not installed")


def settings_in(folder: str | Path, **changes) -> Settings:
    """Settings that keep everything the engine writes inside `folder`."""
    home = Path(folder)
    return Settings(home=home, work_dir=home / "work", models_dir=home / "models", **changes)


class DatabaseTest(unittest.TestCase):
    """A test whose every statement is undone when it ends."""

    def setUp(self):
        url = os.environ.get(TEST_DATABASE_VARIABLE)
        if not url:
            self.skipTest(f"{TEST_DATABASE_VARIABLE} is not set")
        if importlib.util.find_spec("pg8000") is None:
            self.skipTest("pg8000 is not installed")
        self.database = Database.from_url(url)
        self.connection = db.driver().connect(self.database, autocommit=False)
        scope = db.use(self.connection)
        scope.__enter__()
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.addCleanup(scope.__exit__, None, None, None)

    def query(self, sql, params=()):
        return db.cursor().execute(sql, params).fetchall()


def make_video(path: Path, colours=("red", "0x103818", "0xd0d8ff"), seconds: float = 1.0,
               size: str = "160x90", rate: int = 25) -> Path:
    """A video of flat colours, one per shot, with a tone under it.

    The default is bright red, then dark green, then pale blue.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for colour in colours:
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s={size}:r={rate}:d={seconds}"]
    inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds * len(colours)}"]
    joined = "".join(f"[{n}:v]" for n in range(len(colours)))
    ffmpeg.encode(
        *inputs, "-filter_complex", f"{joined}concat=n={len(colours)}:v=1[v]",
        "-map", "[v]", "-map", f"{len(colours)}:a", "-c:v", "{vcodec}", "-b:v", "1M",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path,
    )
    return path


def make_tone(path: Path, seconds: float = 0.5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", f"sine=frequency=440:duration={seconds}", path)
    return path


def make_still(path: Path, size: str = "160x90") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", f"testsrc=s={size}", "-frames:v", "1", "-update", "1", path)
    return path


_SYLLABLES = ("ba", "ko", "mi", "ru", "te", "la", "no", "si", "du", "fe", "ga", "po", "vi", "zu", "he")
#: Six hundred made-up words. Nothing here is anyone's speech.
VOCABULARY = tuple(
    a + b + c for a in _SYLLABLES for b in _SYLLABLES[:8] for c in _SYLLABLES[:5]
)


def make_talk(seconds: float, seed: int = 1) -> list[tuple[str, float]]:
    """A stretch of made-up speech: `(word, time)` at about two words a second."""
    import random

    chance = random.Random(seed)
    talk, now = [], 0.0
    while now < seconds:
        talk.append((chance.choice(VOCABULARY), round(now, 3)))
        now += chance.uniform(0.3, 0.7)
    return talk


def heard(talk, began_s: float, length_s: float, *, miss: float = 0.1, jitter_s: float = 0.03,
          seed: int = 2) -> list[tuple[str, float]]:
    """What a device that rolled from `began_s` for `length_s` wrote down of a talk.

    Times are the device's own, from zero. It misses some words and is a
    little out on when each began, as two transcripts of one room are.
    """
    import random

    chance = random.Random(seed)
    return [
        (word, round(max(0.0, when - began_s + chance.uniform(-jitter_s, jitter_s)), 3))
        for word, when in talk
        if began_s <= when < began_s + length_s and chance.random() >= miss
    ]


def write_transcript(asset_id, words) -> None:
    """Store `(word, time)` pairs as an asset's transcript."""
    words = list(words)
    if not words:
        return
    cur = db.cursor()
    cur.execute(
        """INSERT INTO media_video.segments (asset_id, idx, start_s, end_s, text)
           VALUES (%s, 0, %s, %s, %s) RETURNING id""",
        (asset_id, words[0][1], words[-1][1] + 0.3, " ".join(word for word, _ in words)),
    )
    segment = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO media_video.words (segment_id, idx, start_s, end_s, word)
           SELECT %s, w.idx, w.start_s, w.start_s + 0.25, w.word
             FROM jsonb_to_recordset(%s) AS w(idx integer, start_s double precision, word text)""",
        (segment, db.jsonb([{"idx": n, "start_s": when, "word": word} for n, (word, when) in enumerate(words)])),
    )


def write_lines(asset_id, lines, *, pace_s: float = 0.3) -> list:
    """Store `(start, text)` or `(start, text, fields)` as an asset's transcript, a line each.

    The words of a line follow one another every `pace_s`, a quarter of a
    second long. `fields` are other columns of the line: `speaker`,
    `avg_logprob`, `no_speech_prob`. Returns the ids of the lines.
    """
    cur = db.cursor()
    cur.execute("SELECT coalesce(max(idx) + 1, 0) FROM media_video.segments WHERE asset_id = %s", (asset_id,))
    first = cur.fetchone()[0]
    stored = []
    for at, (start, text, *rest) in enumerate(lines):
        fields = rest[0] if rest else {}
        words = text.split()
        cur.execute(
            """INSERT INTO media_video.segments
                      (asset_id, idx, start_s, end_s, text, speaker, avg_logprob, no_speech_prob)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (asset_id, first + at, start, start + (len(words) - 1) * pace_s + 0.25, text,
             fields.get("speaker"), fields.get("avg_logprob"), fields.get("no_speech_prob")),
        )
        stored.append(cur.fetchone()[0])
        cur.execute(
            """INSERT INTO media_video.words (segment_id, idx, start_s, end_s, word)
               SELECT %s, w.idx, w.start_s, w.start_s + 0.25, w.word
                 FROM jsonb_to_recordset(%s) AS w(idx integer, start_s double precision, word text)""",
            (stored[-1], db.jsonb([{"idx": n, "start_s": start + n * pace_s, "word": word}
                                   for n, word in enumerate(words)])),
        )
    return stored


#: Made-up voices: each "speaks" one wavering note, in hertz.
NOTES = {"ada": 220.0, "ben": 660.0, "cy": 1980.0}


def spoken(who: str, seconds: float, rate: int = 16000):
    """A stretch of one made-up voice, as samples from -1 to 1.

    The note wavers four times a second, as no steady tone does and every
    voice does: a sound that never changes has nothing in it to tell apart.
    """
    import numpy as np

    time = np.arange(round(seconds * rate)) / rate
    waver = 0.15 * NOTES[who] / 4 * np.sin(2 * np.pi * 4 * time)
    return (0.5 * np.sin(2 * np.pi * NOTES[who] * time + waver)).astype(np.float32)


def make_voices(path: Path, lines, rate: int = 16000) -> list[tuple[float, float]]:
    """Write a recording of `(who, seconds)` lines and return when each is spoken.

    The lines follow one another with half a second of quiet between them.
    `who` may be a tuple of voices, speaking one after the other in the line.
    """
    import wave

    import numpy as np

    sound, spans, now = [], [], 0.0
    for who, seconds in lines:
        people = who if isinstance(who, tuple) else (who,)
        line = np.concatenate([spoken(person, seconds / len(people), rate) for person in people])
        sound += [line, np.zeros(rate // 2, dtype=np.float32)]
        spans.append((round(now, 3), round(now + len(line) / rate, 3)))
        now += len(line) / rate + 0.5
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes((np.concatenate(sound) * 32767).astype("<i2").tobytes())
    return spans


class ByNote:
    """A stand-in embedder: where in the spectrum a sound's energy lies.

    It tells the made-up voices apart as a real model tells people apart, and
    counts what it is asked.
    """

    name = "by-note"
    model = "by-note-1"
    EDGES = (0, 400, 1200, 4000, 8000)

    def __init__(self):
        self.asked = 0

    def embed(self, sounds):
        import numpy as np

        rows = []
        for sound in sounds:
            self.asked += 1
            power = np.abs(np.fft.rfft(sound)) ** 2
            hertz = np.fft.rfftfreq(len(sound), 1 / 16000)
            row = np.array([power[(hertz >= low) & (hertz < high)].sum()
                            for low, high in zip(self.EDGES, self.EDGES[1:])]) ** 0.5
            rows.append(row / max(np.linalg.norm(row), 1e-12))
        return np.stack(rows).astype(np.float32)


def voiced(seconds: float, rate: int = 16_000, seed: int = 1):
    """A made-up voice: a note that wanders, with overtones, louder and quieter three times a second."""
    import numpy as np

    chance = np.random.default_rng(seed)
    time = np.arange(int(seconds * rate)) / rate
    pitch = 120 + 40 * np.sin(2 * np.pi * 0.7 * time + seed) + np.cumsum(chance.standard_normal(len(time))) * 0.05
    sound = sum(np.sin(2 * np.pi * np.cumsum(pitch * n) / rate) / n for n in range(1, 6))
    sound = sound * (0.3 + 0.2 * np.sin(2 * np.pi * 3 * time))
    return (0.2 * sound + 0.01 * chance.standard_normal(len(time))).astype(np.float32)


def syllables(seconds: float, rate: int = 24_000, seed: int = 1):
    """Made-up talk: one syllable after another, each a note that glides, no two alike.

    A steady note is the same a moment later, which is what a stutter sounds
    like. Talk is not: its pitch is always on the way somewhere.
    """
    import numpy as np

    chance = np.random.default_rng(seed)
    sound = np.zeros(int(seconds * rate), dtype=np.float32)
    at = 0
    while at < len(sound):
        length = int(chance.uniform(0.18, 0.32) * rate)
        time = np.arange(length) / rate
        pitch = chance.uniform(100, 200) + chance.choice((-1, 1)) * chance.uniform(200, 380) * time
        weights = chance.uniform(0.2, 1.0, 5)
        note = sum(w * np.sin(2 * np.pi * np.cumsum(np.maximum(pitch, 40) * n) / rate + chance.uniform(0, 6.28)) / n
                   for n, w in enumerate(weights, 1))
        note = 0.1 * note * np.hanning(length)
        room = min(length, len(sound) - at)
        sound[at:at + room] += note[:room].astype(np.float32)
        # The next begins while this one is still sounding: there is no quiet between them.
        at += length // 2
    return sound + (0.004 * chance.standard_normal(len(sound))).astype(np.float32)


class Speaker:
    """A stand-in for a speech model. It writes made-up talk, longer the more words there are.

    `wrong` is asked about every reading, with the words and the seed, and
    says what goes wrong with it: nothing, or "stutter" (a moment comes
    twice), "cut" (it stops mid-sound), "empty" (a file with nothing in it),
    or an exception to raise.
    """

    name, model = "stand-in", "made-up-voice"

    def __init__(self, wrong=None, rate: int = 24_000):
        self.wrong = wrong or (lambda text, seed: None)
        self.rate = rate
        self.asked: list[tuple[str, object, int | None]] = []
        self.said: dict[str, tuple[str, int | None]] = {}

    def say(self, text, target, *, voice, seed=None):
        import numpy as np

        from constructive_media.audio import pcm

        self.asked.append((text, voice, seed))
        self.said[str(target)] = (text, seed)
        wrong = self.wrong(text, seed)
        if isinstance(wrong, Exception):
            raise wrong
        target.parent.mkdir(parents=True, exist_ok=True)
        if wrong == "empty":
            target.write_bytes(b"")
            return target
        rate = self.rate
        seconds = 0.6 + 0.3 * len(text.split())
        sound = syllables(max(seconds, 3.4) if wrong else seconds, rate, seed=(seed or 0) + len(text))
        edge = int(0.25 * rate)
        sound[-edge:] *= np.linspace(1, 0, edge, dtype=np.float32) ** 3
        if wrong == "cut":
            # The last sound is still at its loudest when the file ends.
            sound = np.concatenate([sound, voiced(0.2, rate)])
        if wrong == "stutter":
            # 180 thousandths of a second come twice, 60 apart.
            a, b, c = rate, rate + int(0.18 * rate), rate + int(0.24 * rate)
            sound = np.concatenate([sound[:b], sound[b:c] * 0.02, sound[a:b], sound[b:]])
        pcm.write(target, sound, rate, bits=16)
        return target


class Listener:
    """A stand-in for a speech-to-text model. It hears what the stand-in speaker was given.

    `hears` is asked about every reading, with the words and the seed, and
    says what was heard instead.
    """

    name, model = "stand-in", "made-up-ears"

    def __init__(self, speaker: Speaker, hears=None):
        self.speaker = speaker
        self.hears = hears or (lambda text, seed: text)
        self.listened: list[str] = []

    def transcribe(self, audio, *, language="en", prompt=None):
        from constructive_media.transcription import Segment

        self.listened.append(str(audio))
        text, seed = self.speaker.said[str(audio)]
        return [Segment(0.0, 1.0, self.hears(text, seed))]
