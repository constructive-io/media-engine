"""A voice, from a recording to a line.

A speech model that clones a voice is given one short recording of it and
the words said in that recording. How good the voice is depends on that one
clip far more than on anything done later, so this module is about the clip:

    measure     how long a recording is, how loud, and what is wrong with it
    reference   cut a recording into a clip a model can clone from
    audition    have a voice say lines it has never seen, and count what passed
    say         one line in one voice, checked like any line of a script

What makes a good clip is short to say. One person, one way of speaking, no
music and no room. Between fifteen and twenty-eight seconds: under twelve
there is too little to copy, and over thirty most models stop listening. One
channel at 24 kHz, with the rumble under 70 Hz taken off, at -19 LUFS with
its peaks under -2 dB. It ends on a calm phrase with a breath of quiet after
it, because a model is apt to say the last words of its clip before a line,
and its words are written down exactly, from the clip as cut, not as it was
before.

Nothing here keeps a recording. A clip is written where it is asked for and
stays the business of whoever owns the voice.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..settings import Settings
from ..speech import SpeechError, StoredVoices, Synthesizer, Voice, Voices
from ..speech import synthesizer as speech_adapter
from ..transcription import Transcriber
from ..transcription import transcriber as transcription_adapter
from . import check, pcm, scene

#: What a clip to clone from should be.
GOOD_S = (15.0, 28.0)
LEAST_S = 12.0
MOST_S = 30.0
RATE = 24_000
LOUDNESS = -19.0
TRUE_PEAK = -2.0
HIGHPASS_HZ = 70
#: Quiet at either end of a clip is cut to this, and no closer.
EDGE_S = 0.15
QUIET_DB = -50
#: A stretch this far under full scale, for this long, is a pause; a pause past LONG_PAUSE_S is a hole in a clip.
PAUSE_DB = -40
PAUSE_S = 0.25
LONG_PAUSE_S = 3.0
#: What `tighten` brings a long pause down to when it is not told.
TIGHT_S = 0.8
#: A clip whose last moments are louder than a pause stops in the middle of a sound.
LAST_S = 0.04

#: An audition passes when this share of its lines did, and none began with the clip's own words.
PASSING = 0.8
SEED = 4100
#: Lines no voice was made from: short and long, a question, a list, numbers, a name.
LINES = (
    "Good morning. I did not expect to see you here so early.",
    "Could you tell me which of these two roads goes down to the harbour?",
    "There were three of them: a red one, a green one, and one nobody could describe.",
    "It costs four pounds fifty, or nine pounds for the pair.",
    "Wait.",
    "If the weather holds until Thursday we will take the long way round, past the mill and over the bridge.",
    "I have told you twice already, and I am not going to say it again.",
    "Thank you. That is everything, I think.",
)


def measure(recording: str | Path) -> dict[str, Any]:
    """What a recording is: how long, how many channels at what rate, and how loud."""
    recording = Path(recording)
    if not recording.is_file():
        raise SpeechError(f"{recording}: no such file")
    seconds = pcm.seconds(recording)
    if not seconds:
        raise SpeechError(f"{recording} holds no sound")
    channels, rate = pcm.shape(recording)
    heard = scene.metered(recording)
    return {"file": str(recording), "seconds": round(seconds, 2), "channels": channels, "rate": rate,
            "loudness": heard and round(heard["loudness"], 1), "peak": heard and round(heard["peak"], 1),
            "pause": round(max(pauses(recording), default=0.0), 2), "cut_off": cut_off(recording)}


def cut_off(recording: str | Path) -> bool:
    """Whether a recording stops in the middle of a sound, with no quiet after its last word."""
    import numpy

    samples, rate = pcm.read(Path(recording), stereo=False), pcm.rate(Path(recording))
    last = samples[-max(1, int(LAST_S * rate)):]
    return bool(len(last)) and float(numpy.abs(last).max()) > 10 ** (PAUSE_DB / 20)


def quiets(recording: str | Path) -> list[tuple[float, float]]:
    """Where a recording goes quiet for `PAUSE_S` or longer after it has begun: each as (from, to), in seconds."""
    said = ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-nostats", "-i", recording, "-vn",
                      "-af", f"silencedetect=noise={PAUSE_DB}dB:d={PAUSE_S}", "-f", "null", "-").stderr
    starts = [float(one) for one in re.findall(r"silence_start: (-?[\d.]+)", said)]
    ends = [float(one) for one in re.findall(r"silence_end: ([\d.]+)", said)]
    return [(start, end) for start, end in zip(starts, ends) if start > 0.05]


def pauses(recording: str | Path) -> list[float]:
    """How long each pause in a recording is, in seconds. The quiet it ends in is not one."""
    ends = pcm.seconds(Path(recording)) - 0.05
    return [end - start for start, end in quiets(recording) if end < ends]


def tightened(recording: str | Path, target: str | Path, seconds: float) -> float:
    """Write `recording` as `target` with every pause longer than `seconds` shortened to that. What was taken out.

    The middle of a long pause is what goes: the way the words before it die
    away and the breath before the next are kept, half of `seconds` each, and
    joined over a hundredth of a second so that the join is not heard.
    """
    import numpy

    samples, rate = pcm.read(Path(recording), stereo=False), pcm.rate(Path(recording))
    blend = max(1, int(0.01 * rate))
    kept, at, taken = [], 0, 0.0
    for start, end in quiets(recording):
        if end - start <= seconds or end >= len(samples) / rate - 0.05:      # the quiet it ends in is no pause
            continue
        upto, again = int((start + seconds / 2) * rate), int((end - seconds / 2) * rate)
        if not at <= upto < again <= len(samples):
            continue
        kept.append(samples[at:upto])
        at, taken = again, taken + (again - upto) / rate
    kept.append(samples[at:])
    whole = kept[0]
    for piece in kept[1:]:
        over = min(blend, len(whole), len(piece))
        ramp = numpy.linspace(0.0, 1.0, over, dtype=samples.dtype)
        joined = whole[len(whole) - over:] * (1 - ramp) + piece[:over] * ramp
        whole = numpy.concatenate([whole[:len(whole) - over], joined, piece[over:]])
    pcm.write(Path(target), whole, rate)
    return round(taken, 2)


def fit(measured: dict[str, Any]) -> tuple[list[str], list[str]]:
    """What stops a recording being a clip to clone from, and what is only worth knowing."""
    stops, notes = [], []
    seconds = measured["seconds"]
    if measured["loudness"] is None:
        stops.append("there is nothing to hear in it")
    if seconds > MOST_S:
        stops.append(f"it runs {seconds:.1f} seconds, and past {MOST_S:.0f} most models stop listening: "
                     f"cut {GOOD_S[0]:.0f} to {GOOD_S[1]:.0f} seconds of it with --from and --to")
    elif seconds < LEAST_S:
        notes.append(f"it runs {seconds:.1f} seconds, and under {LEAST_S:.0f} there is too little of a voice to copy")
    elif not GOOD_S[0] <= seconds <= GOOD_S[1]:
        notes.append(f"it runs {seconds:.1f} seconds; {GOOD_S[0]:.0f} to {GOOD_S[1]:.0f} is what clones best")
    if measured.get("pause", 0) > LONG_PAUSE_S:
        notes.append(f"there is a pause of {measured['pause']:.1f} seconds in it. A model copies a clip's pace, "
                     "and may stop where the clip stops: shorten its pauses with --tighten")
    if measured.get("cut_off"):
        notes.append("it stops in the middle of a sound. A model goes on from where its clip stops, and says the "
                     "rest before a line: end it in the quiet after a sentence, with --to")
    if measured["channels"] != 1:
        notes.append(f"it has {measured['channels']} channels; a clip to clone from has one")
    if measured["rate"] < RATE:
        notes.append(f"it is at {measured['rate']} Hz, under the {RATE} a clip is kept at: nothing above "
                     f"{measured['rate'] // 2} Hz is in it")
    return stops, notes


def reference(
    recording: str | Path, target: str | Path, *, start: float | None = None, end: float | None = None,
    said: str | None = None, transcriber: Transcriber | None = None, language: str = "en",
    tighten: float | None = None,
) -> dict[str, Any]:
    """Cut `recording` into a clip a voice can be cloned from, written as `target`.

    The part from `start` to `end` is taken, its rumble and the quiet at its
    ends cut off, and it is brought to one channel at the rate and loudness a
    clip is kept at. Its words are `said`; with a `transcriber` and no words
    given they are what the transcriber hears in the clip as it was cut.
    They are written beside the clip, in a file of the same name ending
    `.txt`, for a person to read against the sound and put right.

    With `tighten`, every pause in it longer than that many seconds is
    shortened to that many: for a recording with a hole in it, where a
    passage was cut away or somebody else spoke.

    A SpeechError when the clip would be too long to clone from; then
    nothing is written.
    """
    recording, target = Path(recording), Path(target)
    before = measure(recording)
    if start is not None and end is not None and end <= start:
        raise SpeechError("--to must be later than --from")
    part = [*(("-ss", f"{start:g}") if start is not None else ()), *(("-to", f"{end:g}") if end is not None else ())]
    trim = (f"silenceremove=start_periods=1:start_threshold={QUIET_DB}dB:start_silence={EDGE_S}"
            ":detection=peak")
    if tighten is not None and tighten < PAUSE_S:
        raise SpeechError(f"a pause is tightened to {PAUSE_S} seconds or more: less and the words run together")
    with tempfile.TemporaryDirectory(prefix="reference-") as scratch:
        cut = Path(scratch) / "cut.wav"
        ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *part, "-i", recording, "-vn",
                   "-af", f"highpass=f={HIGHPASS_HZ},{trim},areverse,{trim},areverse",
                   "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s24le", cut)
        if tighten is not None:
            whole, cut = cut, Path(scratch) / "tight.wav"
            tightened(whole, cut, tighten)
        made = measure(cut)
        stops, notes = fit(made)
        if stops:
            raise SpeechError(f"{recording} cannot be a clip to clone from: {'; '.join(stops)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        loudness = scene.finish(cut, target, loudness=LOUDNESS, peak=TRUE_PEAK, rate=RATE)
    after = measure(target)
    heard = None
    if said is None and transcriber is not None:
        heard = " ".join(segment.text.strip() for segment in transcriber.transcribe(target, language=language)).strip()
    words = (said if said is not None else heard or "").strip()
    written = None
    if words:
        written = target.with_suffix(".txt")
        written.write_text(words + "\n", encoding="utf-8")
    return {"output": str(target), "from": before, "is": after, "notes": notes, "said": words or None,
            "said_file": str(written) if written else None, "heard": heard is not None and said is None,
            "how": loudness["how"] if loudness else None}


def say(
    words: str, target: str | Path, *, persona: str, emotion: str | None = None, seed: int | None = None,
    voices: Voices | None = None, synthesizer: Callable[[str | None], Synthesizer] | None = None,
    transcriber: Transcriber | None = None, settings: Settings | None = None, **how: Any,
) -> dict[str, Any]:
    """Say `words` in a persona's voice and write the take that passed as `target`.

    The line is said, listened back to and said again until a take passes,
    as every line of a script is; `how` is passed on to `line.speak`. When
    none passes the best is written all the same, and its status says so.
    """
    from . import line

    if not words.strip():
        raise SpeechError("there are no words to say")
    voice = (voices if voices is not None else StoredVoices()).find(persona, emotion)
    speaks = synthesizer(voice.engine) if synthesizer else speech_adapter(voice.engine, settings=settings)
    listens = transcriber if transcriber is not None else transcription_adapter(settings=settings)
    target = Path(target)
    with tempfile.TemporaryDirectory(prefix="say-") as scratch:
        take = line.speak(words, voice, scratch, synthesizer=speaks, transcriber=listens,
                          seed=SEED if seed is None else seed, **how)
        if take.path is None:
            raise SpeechError(f"nothing could be said: {take.verdict.heard}")
        _write(take.path, target)
    return {"output": str(target), **_told(take, voice)}


def audition(
    persona: str, folder: str | Path, *, emotion: str | None = None, lines: Sequence[str] = LINES,
    seed: int | None = None, voices: Voices | None = None,
    synthesizer: Callable[[str | None], Synthesizer] | None = None, transcriber: Transcriber | None = None,
    settings: Settings | None = None, tell: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Have a voice say lines it was not made from, once each, and say how it did.

    Every line is said with one seed and no second try, since a second try
    is what hides a voice that only works half the time. Each is listened
    back to and compared with its words, and what was heard before the line
    is compared with the end of the voice's recording: a voice that says its
    clip's last words first has a clip that ends badly. The takes are left
    in `folder` to be heard.

    `passed` is true when enough lines passed and none began with the
    clip's words.
    """
    from . import line

    lines = [one.strip() for one in lines if one.strip()]
    if not lines:
        raise SpeechError("an audition needs lines to say")
    voice = (voices if voices is not None else StoredVoices()).find(persona, emotion)
    speaks = synthesizer(voice.engine) if synthesizer else speech_adapter(voice.engine, settings=settings)
    listens = transcriber if transcriber is not None else transcription_adapter(settings=settings)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    first = SEED if seed is None else seed
    said = []
    for number, words in enumerate(lines, start=1):
        take = line.speak(words, voice, folder, synthesizer=speaks, transcriber=listens, seed=first + number * 7,
                          name=f"line_{number:02d}", attempts=1, more_seeds=0)
        kept = None
        if take.path is not None:
            kept = folder / f"line_{number:02d}.wav"
            shutil.move(take.path, kept)
        told = {"line": number, "text": words, "file": str(kept) if kept else None, **_told(take, voice),
                "ok": take.ok}
        said.append(told)
        if tell:
            tell(f"  [{number:02d}] " + ("ok" if take.ok else told["reason"])
                 + (f", began with `{told['bled']}`" if told["bled"] else "")
                 + ("" if take.ok else f": heard `{told['heard']}`"))
    good = sum(one["ok"] for one in said)
    bled = [one["line"] for one in said if one["reason"] == check.REFERENCE_BLEED]
    return {"persona": voice.persona, "emotion": voice.emotion, "engine": voice.engine, "profile": voice.profile,
            "folder": str(folder), "lines": said, "good": good, "of": len(said), "bled": bled,
            "passed": not bled and good >= PASSING * len(said)}


def _told(take: Any, voice: Voice) -> dict[str, Any]:
    verdict = take.verdict
    return {"persona": voice.persona, "emotion": voice.emotion, "profile": voice.profile, "seed": take.seed,
            "attempts": take.attempts, "status": take.status, "reason": verdict.reason,
            "word_error": round(verdict.word_error, 3), "heard": verdict.heard, "bled": verdict.bled}


def _write(take: Path, target: Path) -> None:
    """A take, as the kind of file the target's name asks for."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".wav":
        shutil.copyfile(take, target)
    else:
        ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", take, "-vn", target)


def alike(one: str | Path, other: str | Path, *, embedder: Any = None, settings: Settings | None = None) -> float:
    """How alike the voices in two recordings are, from -1 to 1, as the speaker model hears them.

    Each recording is heard whole, as one voice. What counts as the same
    person depends on the model: with the default, two clips of one person
    come out near 0.7 or over, and two people under 0.4.
    """
    from ..speakers import RATE as HEARD_AT
    from ..speakers import embedder as speaker_adapter

    hears = embedder if embedder is not None else speaker_adapter(settings=settings)
    rows = hears.embed([pcm.read(Path(sound), rate=HEARD_AT, stereo=False) for sound in (one, other)])
    return float(sum(float(a) * float(b) for a, b in zip(rows[0], rows[1])))
