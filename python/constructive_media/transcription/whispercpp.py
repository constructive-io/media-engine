"""The default transcriber: Whisper through whisper.cpp (MIT).

whisper.cpp is run as a separate program, `whisper-cli`, and is not part of
this package; nothing here imports a model library. The models it knows by
name are OpenAI's Whisper weights (MIT) in whisper.cpp's file format.

`CONSTRUCTIVE_MEDIA_MODEL_TRANSCRIPTION` picks the model: a name from `KNOWN`,
or the path of a model file in that format.

Five things are done on purpose:

- Only speech is transcribed. The stretches where nobody speaks are found
  first and cut out, because Whisper writes sentences over noise.
- Whisper listens thirty seconds at a time and loses words that straddle the
  edge of a window. So the speech is handed over in pieces that fit one
  window and end on a pause, wherever the speakers leave one.
- Nothing already written is fed back in. With its own output as context
  Whisper falls into loops, one line repeated for minutes.
- A vocabulary is shown only to pieces that fit one window. Shown a
  vocabulary, Whisper stops marking time as it writes, and a stretch longer
  than a window then loses whatever it had not reached. The smallest models
  are never shown one: over noisy speech they lose a fifth of the words to it.
- Word times come from the model's attention, not its timestamp guesses:
  they land on the word's edges far more often.

No weights ship with this repository. A known model is downloaded once into
the models folder and checked against a pinned hash.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..models import ModelError, Remote, fetch
from ..settings import Settings
from . import Segment, TranscriptionError, Word, regions

DEFAULT_MODEL = "large-v3-turbo"
PROGRAM = "whisper-cli"
SPEECH_PROGRAM = "whisper-vad-speech-segments"

#: What Whisper listens to: one channel, 16,000 samples a second.
SAMPLE_RATE = 16_000
#: Whisper's window, and how long a piece of speech may be to sit inside it.
WINDOW_S = 30.0
LONGEST_PIECE_S = 28.0
MAX_THREADS = 8

#: Model families too small to be shown a vocabulary without losing speech.
HEEDLESS = frozenset({"tiny", "tiny.en", "base", "base.en"})

#: Written without spaces between words, so each token is timed on its own.
UNSPACED = frozenset({"zh", "ja", "th", "lo", "my", "yue"})

_WEIGHTS = "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1"


@dataclass(frozen=True)
class Known:
    """A published model: what it is, its license and where its file is."""

    summary: str
    license: str
    file: Remote


def _whisper(file: str, sha256: str, summary: str) -> Known:
    return Known(summary, "MIT", Remote(f"{_WEIGHTS}/{file}", sha256))


KNOWN: dict[str, Known] = {
    "large-v3-turbo": _whisper(
        "ggml-large-v3-turbo.bin", "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
        "Whisper large v3 turbo, 1.6 GB: the most accurate at a speed worth running everywhere"),
    "large-v3-turbo-q5_0": _whisper(
        "ggml-large-v3-turbo-q5_0.bin", "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
        "Whisper large v3 turbo squeezed to 574 MB"),
    "large-v3": _whisper(
        "ggml-large-v3.bin", "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2",
        "Whisper large v3, 3.1 GB: the full model, several times slower than turbo"),
    "medium": _whisper(
        "ggml-medium.bin", "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208",
        "Whisper medium, 1.5 GB"),
    "medium.en": _whisper(
        "ggml-medium.en.bin", "cc37e93478338ec7700281a7ac30a10128929eb8f427dda2e865faa8f6da4356",
        "Whisper medium, English only"),
    "small": _whisper(
        "ggml-small.bin", "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
        "Whisper small, 466 MB"),
    "small.en": _whisper(
        "ggml-small.en.bin", "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
        "Whisper small, English only"),
    "base": _whisper(
        "ggml-base.bin", "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        "Whisper base, 142 MB: fast on a machine with no graphics card"),
    "base.en": _whisper(
        "ggml-base.en.bin", "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
        "Whisper base, English only"),
    "tiny": _whisper(
        "ggml-tiny.bin", "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
        "Whisper tiny, 75 MB: the fastest and the least accurate"),
    "tiny.en": _whisper(
        "ggml-tiny.en.bin", "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f",
        "Whisper tiny, English only"),
}

#: The model that finds where the speech is: Silero VAD (MIT), 885 KB.
SPEECH_MODEL = "ggml-silero-v6.2.0.bin"
SPEECH_REMOTE = Remote(
    "https://huggingface.co/ggml-org/whisper-vad/resolve/"
    f"9ffd54a1e1ee413ddf265af9913beaf518d1639b/{SPEECH_MODEL}",
    "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
)

#: Model families whisper.cpp can read word times from, by its own names.
_ALIGNABLE = frozenset({
    "tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
    "large.v1", "large.v2", "large.v3", "large.v3.turbo",
})


class WhisperCpp:
    """Whisper, run by the `whisper-cli` program.

    `model` is a name from `KNOWN` or the path of a model file; `device` is
    `cpu` to keep off the graphics card. With `speech_only` off the whole
    recording is transcribed, silence and noise included.
    """

    name = "whispercpp"

    def __init__(
        self,
        model: str | None = None,
        device: str | None = None,
        *,
        settings: Settings | None = None,
        speech_only: bool = True,
        program: str = PROGRAM,
        speech_program: str = SPEECH_PROGRAM,
    ) -> None:
        settings = settings or Settings.from_env()
        chosen = model or settings.model("transcription") or DEFAULT_MODEL
        self._chosen = chosen
        #: The model's name: a known name, or a model file's name without its wrapping.
        self.model = chosen if chosen in KNOWN else Path(chosen).stem.removeprefix("ggml-")
        self.device = device or settings.device
        self.speech_only = speech_only
        self.program = program
        self.speech_program = speech_program
        self._models_dir = settings.models_dir / "transcription"

    def weights(self) -> Path:
        """The model file, downloading a known model if needed."""
        if self._chosen in KNOWN:
            remote = KNOWN[self._chosen].file
            return fetch(remote, self._models_dir / remote.url.rpartition("/")[2])
        file = Path(self._chosen).expanduser()
        if not file.is_file():
            raise ModelError(
                f"no transcription model {self._chosen!r}: it is not one of "
                f"{', '.join(KNOWN)}, and there is no file by that name"
            )
        return file

    def alignment(self) -> str | None:
        """The model family whisper.cpp reads word times from, if it knows this one."""
        return self._family if self._family in _ALIGNABLE else None

    @property
    def _family(self) -> str:
        """The model's name as whisper.cpp spells it, whatever size it was squeezed to."""
        return re.sub(r"-q\d+_\d+$", "", self.model).replace("-", ".")

    def transcribe(
        self, audio: str | Path, *, language: str = "en", prompt: str | None = None,
    ) -> list[Segment]:
        weights = self.weights()
        with tempfile.TemporaryDirectory(prefix="transcribe-") as scratch:
            folder = Path(scratch)
            heard = folder / "heard.wav"
            ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", audio,
                       "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", heard)
            pieces: list[tuple[Path, regions.Packed | None]] = [(heard, None)]
            if self.speech_only:
                pieces = [
                    (piece, regions.pack(heard, piece, run))
                    for number, run in enumerate(regions.grouped(self.speech(heard), LONGEST_PIECE_S))
                    if (piece := folder / f"speech-{number:05d}.wav")
                ]
            written = self._written(weights, [piece for piece, _ in pieces], language, prompt)
        aligned, split = self.alignment() is not None, language in UNSPACED
        return [
            segment
            for (_, packed), transcript in zip(pieces, written)
            for segment in _segments(transcript, packed, aligned=aligned, split=split)
        ]

    def _written(self, weights: Path, wavs: Sequence[Path], language: str,
                 prompt: str | None) -> list[list[dict[str, Any]]]:
        """whisper.cpp's segments for each file, the vocabulary shown where it is safe."""
        if not prompt or self._family in HEEDLESS:
            return self._recognise(weights, wavs, language, None)
        fits = [regions.duration(wav) <= WINDOW_S for wav in wavs]
        shown = iter(self._recognise(weights, [w for w, fit in zip(wavs, fits) if fit], language, prompt))
        plain = iter(self._recognise(weights, [w for w, fit in zip(wavs, fits) if not fit], language, None))
        return [next(shown if fit else plain) for fit in fits]

    def speech(self, wav: Path) -> list[regions.Region]:
        """Where the speech is in a WAV file."""
        model = fetch(SPEECH_REMOTE, self._models_dir / SPEECH_MODEL)
        printed = _run(self.speech_program, "-f", wav, "-vm", model, "-np")
        length = regions.duration(wav)
        found = [
            # The program prints hundredths of a second.
            regions.Region(float(start) / 100, float(end) / 100)
            for start, end in re.findall(r"start\s*=\s*([\d.]+),\s*end\s*=\s*([\d.]+)", printed)
        ]
        if any(region.end > length + 1 for region in found):
            raise TranscriptionError(
                f"{self.speech_program} reported speech past the end of the recording; "
                "this version of whisper.cpp prints times this adapter does not understand"
            )
        return regions.joined(found, length, longest=LONGEST_PIECE_S)

    def _recognise(self, weights: Path, wavs: Sequence[Path], language: str,
                   prompt: str | None) -> list[list[dict[str, Any]]]:
        """whisper.cpp's segments for each file, from one run of the program."""
        if not wavs:
            return []
        command: list[str | Path] = [
            "-m", weights, "-l", language, "-np", "-ojf",
            "-t", str(min(MAX_THREADS, os.cpu_count() or 4)),
            *_context(prompt), *self._device(),
        ]
        family = self.alignment()
        if family:
            # whisper.cpp cannot read attention with its fast attention path on.
            command += ["-dtw", family, "-nfa"]
        _run(self.program, *command, *wavs)
        try:
            # Each transcript is written beside its file, as `<file>.json`.
            return [
                json.loads(wav.with_name(wav.name + ".json").read_text(errors="replace")).get("transcription") or []
                for wav in wavs
            ]
        except (OSError, json.JSONDecodeError) as error:
            raise TranscriptionError(f"{self.program} wrote no transcript: {error}") from error

    def _device(self) -> list[str]:
        return ["-ng"] if self.device == "cpu" else []


def _context(prompt: str | None) -> list[str]:
    """What Whisper is shown before it listens: the vocabulary, or nothing.

    `-mc 0` is what keeps its own earlier text out. It also switches the
    prompt off, so it is only passed when there is no prompt; a prompted file
    fits one window, and has next to no earlier text to be shown.
    """
    return ["--prompt", worded(prompt)] if prompt else ["-mc", "0"]


def worded(prompt: str) -> str:
    """A list of terms as Whisper takes it best: introduced, and ended."""
    return f"Glossary: {prompt.strip().rstrip('.')}."


def _run(program: str, *args: str | Path) -> str:
    if shutil.which(program) is None:
        raise TranscriptionError(
            f"the {program!r} program was not found; install whisper.cpp, which provides it"
        )
    done = subprocess.run([program, *map(str, args)], capture_output=True, text=True, errors="replace")
    if done.returncode:
        lines = done.stderr.strip().splitlines()
        raise TranscriptionError(f"{program} failed: {lines[-1] if lines else 'no message'}")
    return done.stdout


def _segments(written: Sequence[dict[str, Any]], packed: regions.Packed | None, *,
              aligned: bool, split: bool) -> list[Segment]:
    """whisper.cpp's segments as `Segment`s on the recording's own clock."""
    segments = []
    floor = 0.0  # where the segment before ended: no segment starts before that
    for raw in written:
        tokens = [token for token in raw.get("tokens") or [] if not _is_marker(token.get("text", ""))]
        text = (raw.get("text") or "").strip()
        if not tokens or not text:
            continue
        start = max(floor, raw["offsets"]["from"] / 1000)
        end = max(start, raw["offsets"]["to"] / 1000)
        words = _words(tokens, start, aligned=aligned, split=split)
        if words:
            end = max(start, words[-1].end)
        floor = end
        if packed is not None:
            words = [replace(word, start=at, end=until)
                     for word in words for at, until in [packed.span(word.start, word.end)]]
            start, end = packed.original(start), packed.original(end)
        if words:
            start, end = words[0].start, max(words[0].start, words[-1].end)
        sureness = [math.log(max(float(token.get("p", 0.0)), 1e-9)) for token in tokens]
        segments.append(Segment(start, end, text, tuple(words), avg_logprob=sum(sureness) / len(sureness)))
    return segments


def _is_marker(text: str) -> bool:
    """Whether a token is one of Whisper's own marks rather than something said."""
    return text.startswith("[_") and text.endswith("]")


def _words(tokens: Sequence[dict[str, Any]], start: float, *, aligned: bool, split: bool) -> list[Word]:
    """Tokens gathered into words. A word begins at a token that begins with a space.

    When the tokens carry attention times, each marks where its token ends:
    a word ends where its last token ends and begins where the word before
    it ended. Otherwise the tokens' own estimated spans are used.
    """
    groups: list[list[dict[str, Any]]] = []
    for token in tokens:
        if not groups or split or token["text"].startswith(" "):
            groups.append([])
        groups[-1].append(token)
    words = []
    for group in groups:
        text = "".join(token["text"] for token in group).strip()
        ends_at = group[-1].get("t_dtw", -1)
        if aligned and ends_at >= 0:
            end = max(start, ends_at / 100)
        else:
            start = max(start, group[0]["offsets"]["from"] / 1000)
            end = max(start, group[-1]["offsets"]["to"] / 1000)
        if text:
            sure = [max(float(token.get("p", 0.0)), 1e-9) for token in group]
            words.append(Word(start, end, text, math.exp(sum(map(math.log, sure)) / len(sure))))
        start = end
    return words
