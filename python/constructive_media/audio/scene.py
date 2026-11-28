"""A script rendered as sound.

    report = scene.render("night.conv", "out/night.flac", stems=True)
    scene.record(report)

A script is walked from top to bottom against a clock. A line is said,
checked, and said again until a take of it passes; it is put on its track
where the clock stands, and the clock moves on by its length and a gap. A
pause moves the clock. A sound is put where the clock stands, or where its
`at:` says, and the scene waits for it only when it holds.

When every line is in, each sound is given its length: to its own end, to its
`for:`, or to whatever stops it, which is an `off` or, on a track that plays
one thing at a time, the next sound there. A bed that nothing stops plays to
the end of the scene. Then the tracks are mixed, and the mix is turned up or
down whole to one loudness.

Beside the output, and named after it:

    night.lines/line_001.wav   every line as it was said
    night.stems/<track>.wav    each track on its own, when asked for
    night.manifest.json        what was said, with which seed, how it was judged, and where it all landed

Before anything is said the script is held against this machine: every sound
it plays must be a file that is there with sound in it, and everybody who
speaks must have a voice. All that is wrong is reported at once, by line, the
way a script's own mistakes are.

A sound with a `prompt:` and no file is made from its words by whatever fills
the `audio` slot, once: the next render of the same words is handed the sound
that was made before.

`render` writes files and nothing else, but for a sound it has made from a
prompt, which is on record like anything else a model made. `record` writes a
render down in the database: the render, its lines, and the lines a person
should listen to.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import conv, db, ffmpeg
from .. import effects as effects_slot
from ..effects import Effects
from ..ffmpeg import FfmpegError
from ..settings import Settings
from ..speech import SpeechError, StoredVoices, Synthesizer, Voices
from ..speech import synthesizer as speech_adapter
from ..transcription import Transcriber
from ..transcription import transcriber as transcription_adapter
from . import line as lines
from . import mix, pcm

#: Seconds of quiet after every line.
GAP = 0.45
#: How loud the finished scene is, in LUFS, and the most its peaks may reach, in dB.
LOUDNESS = -16.0
TRUE_PEAK = -1.5
#: Seconds a line that could not be said at all is quiet for, so the scene keeps its shape.
QUIET = 0.4
#: Seconds the scene waits for a held sound whose length nothing tells.
HELD = 2.0
#: A sound started partway through fades in for at least this long.
EASED_IN = 1.5
#: The seed of line `n` is `FIRST_SEED + n * SEED_STEP`, so a line keeps its seed when others change.
FIRST_SEED, SEED_STEP = 500, 7

_CODECS: dict[str, tuple[str, ...]] = {
    ".wav": ("-c:a", "pcm_s24le"),
    ".mp3": ("-c:a", "libmp3lame", "-q:a", "2"),
}
_EFFECT_KEYS = tuple(known.name for known in fields(Effects))
#: The loudness meter, saying only its summary: loudness, range and true peak.
_METER = "ebur128=peak=true:framelog=verbose"


@dataclass(frozen=True)
class Ready:
    """What a script needs, as it was found here."""

    files: dict[str, Path]              # a file as the script writes it, and where it is
    probes: dict[str, ffmpeg.Probe]     # what each of them holds
    left_out: tuple[str, ...] = ()      # sounds that are made from a prompt, and were asked to be left out
    notes: tuple[str, ...] = ()         # worth saying, not worth stopping for
    made: dict[str, Path] = field(default_factory=dict)   # sounds that were made from a prompt, and where each is


class Prompted:
    """Sounds made from their prompts by whatever fills the `audio` slot.

    Each is a request like any other made of a model: put on record, made
    once, and handed back the next time the same words are asked for.
    """

    def __init__(self, settings: Settings | None = None, tell: Callable[[str], None] | None = None) -> None:
        self._settings, self._tell, self._maker = settings, tell or (lambda message: None), None

    def _request(self, spec: dict[str, Any]) -> Any:
        from .. import generate

        seconds, seed = spec.get("duration"), spec.get("seed")
        return generate.Request("audio", str(spec.get("prompt") or ""), seconds=float(seconds) if seconds else None,
                                seed=None if seed is None else int(seed))

    def maker(self) -> Any:
        from .. import generate

        if self._maker is None:
            self._maker = generate.maker_for("audio", settings=self._settings)
        return self._maker

    def cannot(self, name: str, spec: dict[str, Any]) -> str | None:
        """Why this sound cannot be made here, when that can be told without trying."""
        try:
            check = getattr(self.maker(), "check", None)
            if check is not None:
                check(self._request(spec))
        except (LookupError, RuntimeError) as error:
            return str(error)
        return None

    def __call__(self, name: str, spec: dict[str, Any]) -> Path:
        from .. import generate

        asked = self._request(spec)
        if generate.make(asked, maker=self.maker(), dry_run=True, settings=self._settings).state == generate.WOULD:
            self._tell(f"  {name}: being made from its prompt")
        done = generate.make(asked, maker=self.maker(), settings=self._settings)
        if done.state == generate.WAITING:
            raise generate.GenerateError(f"{done.backend} has been asked for it and has not made it yet; "
                                         "`media collect` brings it in, and then this can be rendered")
        return done.file


def folders(script: str | Path, sounds: Iterable[str | Path] = (), shared: Iterable[str | Path] = ()) -> list[Path]:
    """Where a script's sound files are looked for, in order: the folders
    given, the script's own folder, the working folder, then the `shared`
    folders, which are those of the libraries that were added."""
    found: list[Path] = []
    for folder in (*(Path(given).expanduser() for given in sounds), Path(script).resolve().parent, Path.cwd(),
                   *(Path(given) for given in shared)):
        if folder not in found:
            found.append(folder)
    return found


def find(file: str, among: Sequence[Path]) -> Path | None:
    """The file a script names, in the first folder that has it."""
    written = Path(file).expanduser()
    if written.is_absolute():
        return written if written.is_file() else None
    return next((folder / written for folder in among if (folder / written).is_file()), None)


def ready(scene: conv.Scene, *, voices: Voices, among: Sequence[Path], text: str = "",
          without_generated: bool = False, source: str | None = None,
          prompted: Callable[[str, dict[str, Any]], Path] | None = None) -> Ready:
    """Hold a script against this machine before anything is said.

    Every sound the script plays must be a file that is found and has sound
    in it; what plays on the `video` track must have a picture. Everybody
    who speaks must have a voice. A `ConvError` carries all that is wrong.

    A sound with a prompt and no file is made by `prompted`, which is given
    its name and what the script says of it and returns the file. It is made
    only when nothing else is wrong, and from then on the scene has it as a
    file like any other. With `without_generated` such sounds are left out
    and listed; with nothing to make them they are a problem. An emotion a
    persona has no voice for is only noted: the line is said in another of
    its voices.
    """
    script = text.splitlines()
    problems: list[conv.Problem] = []

    def problem(line: int | None, message: str) -> None:
        written = script[line - 1].strip() if line and line <= len(script) else ""
        problems.append({"line": line, "text": written, "message": message})

    files: dict[str, Path] = {}
    probes: dict[str, ffmpeg.Probe] = {}
    left_out: list[str] = []
    to_make: dict[str, int | None] = {}
    seen: set[tuple[str, bool]] = set()
    bad: set[str] = set()
    for at, track, sound, file in _played(scene):
        name, on_screen = sound or file or "", track == "video"
        if (name, on_screen) in seen:
            continue
        seen.add((name, on_screen))
        spec = scene["sounds"].get(sound) or {}
        written = file or spec.get("file")
        if not written:
            if sound not in scene["sounds"] or sound in left_out:
                continue
            otherwise = ("Give it a `file:`, or render without such sounds (`without_generated`, "
                         "--without-generated on the command line)")
            if without_generated:
                left_out.append(sound)
            elif prompted is None:
                problem(at, f"`{sound}` is made from a prompt, and nothing was given to make it with.\n{otherwise}")
            elif sound not in to_make:
                cannot = getattr(prompted, "cannot", None)
                why = cannot(sound, spec) if cannot is not None else None
                if why:
                    problem(at, f"`{sound}` is made from a prompt, and cannot be made here: {why}\n{otherwise}")
                else:
                    to_make[sound] = at
            continue
        if written in bad:
            continue
        if written not in files:
            path = find(written, among)
            if path is None:
                bad.add(written)
                looked = "" if Path(written).expanduser().is_absolute() else \
                    "\nLooked in: " + ", ".join(str(folder) for folder in among)
                problem(at, f"`{name}` plays `{written}`, and there is no such file.{looked}")
                continue
            probe = ffmpeg.probe(path)
            if probe is None:
                bad.add(written)
                problem(at, f"`{name}` plays `{written}`, which holds nothing that can be played")
                continue
            files[written], probes[written] = path, probe
        if on_screen and probes[written].medium != "video":
            problem(at, f"`[video {name}]` needs a file with a picture, and `{written}` has only sound")
        elif not on_screen and not probes[written].has_audio:
            problem(at, f"`{name}` plays `{written}`, which has no sound in it")

    have = voices.emotions()
    notes: list[str] = []
    checked: set[str] = set()
    for item in scene["items"]:
        if item["type"] != "line":
            continue
        speaker, persona = item["speaker"], _persona(scene, item["speaker"])
        if persona not in have:
            if persona in checked:
                continue
            checked.add(persona)
            message = f"`{speaker}` speaks as `{persona}`, and `{persona}` has no voice"
            message += conv.did_you_mean(persona, have) or "."
            if speaker not in scene["characters"]:
                message += f"\n`{speaker}` is not under @characters, so its persona is its name in small letters."
            problem(item["line"], message + f"\nVoices: {', '.join(sorted(have)) or 'none yet'}")
            continue
        character = scene["characters"].get(speaker) or {}
        asked = [item["overrides"].get("emotion", character.get("emotion"))]
        asked += [piece["overrides"].get("emotion") for piece in item["segments"] if piece["type"] == "text"]
        for emotion in asked:
            note = f"{persona} has no {emotion} voice; such lines are said in another of its voices"
            if emotion and emotion not in have[persona] and note not in notes:
                notes.append(note)
    if problems:
        raise conv.ConvError(problems, source)
    # Made last, when nothing else is wrong: a model is not run for a script that cannot be rendered.
    made: dict[str, Path] = {}
    for sound, at in to_make.items():
        try:
            path = Path(prompted(sound, scene["sounds"][sound])).resolve()   # type: ignore[misc]
        except Exception as error:
            problem(at, f"`{sound}` could not be made from its prompt: {error}")
            continue
        probe = ffmpeg.probe(path)
        if probe is None or not probe.has_audio:
            problem(at, f"`{sound}` was made from its prompt as `{path}`, which has no sound in it")
            continue
        scene["sounds"][sound]["file"] = str(path)
        files[str(path)], probes[str(path)], made[sound] = path, probe, path
    if problems:
        raise conv.ConvError(problems, source)
    return Ready(files, probes, tuple(left_out), tuple(notes), made)


def render(
    script: str | Path, target: str | Path, *, voices: Voices | None = None,
    synthesizer: Callable[[str | None], Synthesizer] | None = None, transcriber: Transcriber | None = None,
    banks: Sequence[str | Path] = (), sounds: Sequence[str | Path] = (), shared: Sequence[str | Path] = (),
    stems: bool = False,
    only: str | Iterable[Any] | None = None, keep: bool = False, gap: float = GAP,
    loudness: float | None = LOUDNESS,
    label: str | None = None, without_generated: bool = False, tell: Callable[[str], None] | None = None,
    backend: str | None = None, settings: Settings | None = None,
    prompted: Callable[[str, dict[str, Any]], Path] | None = None, **how: Any,
) -> dict[str, Any]:
    """Render a script to `target`, in the kind of file its name asks for.

    `voices` is who can speak: the voices in the database unless given.
    `synthesizer` is given the name of a voice's engine and returns what
    speaks it, and `transcriber` listens back; both are the configured
    adapters unless given. `banks` are the folders `@include` looks in, and
    `sounds` the folders sound files are looked for in before the script's
    own, with `shared` the ones looked in after it. `prompted` makes a sound
    from its prompt, and is whatever fills the `audio` slot unless given;
    `without_generated` leaves such sounds out. `how` is passed on to every line: `attempts`, `reword` and the rest
    of what `line.speak` takes.

    `only` renders again just some lines of a scene rendered here before:
    personas, or lines by number, as a list or with commas between. Every
    other line is the one already kept, as long as its words are the same.
    `keep` says none again: every line already said here with the same words is
    kept, which is what a change to the music or the sounds wants, and only a
    line that is new or has changed is said.

    `loudness` is in LUFS; None leaves the mix as it came. `tell` is told
    about each line as it is done. Returns the report, which is also written
    as the manifest.
    """
    script, target = Path(script), Path(target)
    said = tell or (lambda message: None)
    scene = conv.load(script, banks=[str(folder) for folder in banks])
    voices = voices if voices is not None else StoredVoices()
    found = ready(scene, voices=voices, among=folders(script, sounds, shared), text=conv.read(script),
                  without_generated=without_generated, source=str(script),
                  prompted=prompted or Prompted(settings, said))
    _writable(target)
    for note in found.notes:
        said(f"  {note}")

    base = target.with_suffix("")
    kept, manifest = Path(f"{base}.lines"), Path(f"{base}.manifest.json")
    kept.mkdir(parents=True, exist_ok=True)
    spoken = [item for item in scene["items"] if item["type"] == "line" and _text(item)]
    chosen = _only(only, {_persona(scene, item["speaker"]) for item in spoken}, len(spoken))
    if chosen is None and keep:
        chosen = set()
    before: dict[str, Any] = {}
    if chosen is not None and manifest.is_file():
        before = json.loads(manifest.read_text(encoding="utf-8"))
    elif chosen is not None:
        said("  nothing was rendered here before, so every line is said")

    mixer = mix.Mixer(mix.tracks(scene["tracks"]), backend=backend, settings=settings)
    with tempfile.TemporaryDirectory(prefix="scene-") as scratch:
        walk = _Walk(scene, found, mixer, gap=gap, kept=kept, scratch=Path(scratch), voices=voices,
                     synthesizer=synthesizer or (lambda engine: speech_adapter(engine, settings=settings)),
                     transcriber=transcriber, chosen=chosen,
                     before={old["line"]: old for old in before.get("lines", [])},
                     said=said, backend=backend, settings=settings, how=how)
        for item in scene["items"]:
            walk.step(item)
        placed, missing = _place(mixer, walk.plays, walk.stops, scene["sounds"], found, walk.clock)
        mixer.least = walk.clock
        done = mixer.mix()
        mixed = pcm.write(Path(scratch) / "mix.wav", done.samples, done.rate, bits=32)
        written = mix.write_stems(done, f"{base}.stems") if stems else {}
        loud = finish(mixed, target, loudness=loudness, rate=done.rate)

    old = before.get("speech") or {}
    reused = any(record.get("reused") for record in walk.records)
    report = {
        "label": label or target.stem,
        "script": str(script.resolve()),
        "output": str(target.resolve()),
        "manifest": str(manifest.resolve()),
        "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": done.report["seconds"],
        "speech": {
            "adapters": sorted({one.name for one in walk.spoke.values()} | set(old.get("adapters", []) if reused else [])),
            "models": sorted({one.model for one in walk.spoke.values()} | set(old.get("models", []) if reused else [])),
        },
        "listening": {"adapter": walk.transcriber.name, "model": walk.transcriber.model}
                     if walk.transcriber is not None else (before.get("listening") if reused else None),
        "gap": gap,
        "loudness": loud,
        "needs_review": sum(record["status"] == lines.NEEDS_REVIEW for record in walk.records),
        "lines": walk.records,
        "marks": walk.marks,
        "sound": dict(done.report, sounds=placed, left_out=sorted({*found.left_out, *missing}),
                      from_prompts={name: str(path) for name, path in sorted(found.made.items())}),
        # The stems add up to the mix as it was before it was brought to its loudness.
        "stems": {name: str(path.resolve()) for name, path in written.items()} or None,
    }
    manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def finish(mixed: str | Path, target: str | Path, *, loudness: float | None = LOUDNESS,
           peak: float = TRUE_PEAK, rate: int = mix.RATE) -> dict[str, Any] | None:
    """Write a mix as `target`, in the kind of file its name asks for, at `loudness`.

    The mix is metered first. When turning it up or down whole brings it to
    `loudness` with its peaks under `peak`, that is all that is done to it,
    and nothing about its sound changes. Only when its peaks would pass
    `peak` is it squeezed to fit. A mix with nothing in it, or `loudness`
    None, is written as it came.

    Returns what was asked, how loud the mix was and is, and how it got
    there: `gain`, or `dynamic` when it had to be squeezed.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    kind = _CODECS.get(target.suffix.lower(), ())
    has = metered(mixed) if loudness is not None else None
    if has is None:
        ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", mixed,
                   "-ar", str(rate), *kind, target)
        return None
    gain = loudness - has["loudness"]
    if has["peak"] + gain <= peak:
        chain, how = f"volume={gain:.2f}dB", "gain"
    else:
        # A range at least as wide as the mix's own: its quiet and loud passages stay as far apart as they can.
        spread = min(20.0, max(7.0, has["range"]))
        chain, how = (f"loudnorm=I={loudness:g}:TP={peak:g}:LRA={spread:g}:measured_I={has['loudness']:g}"
                      f":measured_TP={has['peak']:g}:measured_LRA={has['range']:g}"
                      f":measured_thresh={has['threshold']:g}:linear=false"), "dynamic"
    done = ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-nostats", "-y", "-i", mixed, "-af", f"{chain},{_METER}",
                      "-ar", str(rate), *kind, target)
    after = _metered(done.stderr) or {}
    return {"asked": loudness, "true_peak": peak, "was": has["loudness"], "is": after.get("loudness"),
            "peak": after.get("peak"), "how": how}


def metered(sound: str | Path) -> dict[str, float] | None:
    """How loud a file is: its `loudness` in LUFS, its true `peak`, its `range`. None when it is all quiet."""
    return _metered(ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-nostats", "-i", sound, "-af", _METER,
                               "-f", "null", "-").stderr)


def record(report: dict[str, Any]) -> Any:
    """Write a render down: the render, its lines, and the lines to listen to.

    A render is known by its label; rendered again, it takes the place of
    what was there. Lines that needed a listen and were never judged are
    replaced; a line somebody has judged keeps its judgement. Returns the
    render's id.
    """
    spoken = report["lines"]
    params = {key: value for key, value in report.items() if key not in ("lines", "label")}
    params["lines"] = [{key: one.get(key) for key in ("line", "seed", "status", "attempts", "word_error", "reason")}
                       for one in spoken]
    with db.transaction():
        cur = db.cursor()
        cur.execute(
            "INSERT INTO media_audio.renders (label, script_path, output_path, engine_tts, engine_stt, gap_seconds, params) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (label) DO UPDATE SET script_path = EXCLUDED.script_path, output_path = EXCLUDED.output_path, "
            "engine_tts = EXCLUDED.engine_tts, engine_stt = EXCLUDED.engine_stt, gap_seconds = EXCLUDED.gap_seconds, "
            "params = EXCLUDED.params RETURNING id",
            (report["label"], report["script"], report["output"],
             "+".join(report["speech"]["adapters"]) or "none",
             (report.get("listening") or {}).get("adapter"), report["gap"], db.jsonb(params)),
        )
        render_id = cur.fetchone()[0]
        cur.execute("DELETE FROM media_audio.render_chunks WHERE render_id = %s", (render_id,))
        cur.execute("DELETE FROM media_audio.review_queue WHERE render_id = %s AND verdict IS NULL", (render_id,))
        cur.execute("SELECT slug, id FROM media.personas WHERE slug = ANY(%s)",
                    (sorted({one["persona"] for one in spoken}),))
        people = dict(cur.fetchall())
        cur.execute("SELECT slug FROM media_audio.emotions")
        emotions = {row[0] for row in cur.fetchall()}
        for one in spoken:
            person = people.get(one["persona"])
            cur.execute(
                "INSERT INTO media_audio.render_chunks (render_id, line_no, persona_id, emotion, text, audio_path, seconds) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (render_id, one["line"], person, one["emotion"] if one["emotion"] in emotions else None,
                 one["text"], one["file"], one["seconds"]),
            )
            if one["status"] != lines.NEEDS_REVIEW:
                continue
            cur.execute(
                "INSERT INTO media_audio.review_queue (render_id, render_label, line_no, persona_id, emotion, "
                "intended_text, spoken_text, heard_text, word_error, char_sim, attempts, audio_path) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (render_id, report["label"], one["line"], person, one["emotion"], one["text"],
                 one.get("spoken") or one["text"], one.get("heard"), one.get("word_error"),
                 one.get("similarity"), one.get("attempts") or 1, one["file"]),
            )
    return render_id


@dataclass
class _Play:
    """A sound asked for: on which track, from when, and how."""

    track: str
    sound: str | None
    file: str | None
    start: float
    asked: dict[str, Any]
    order: int

    @property
    def name(self) -> str:
        return self.sound or self.file or ""


@dataclass
class _Stop:
    track: str
    at: float
    sound: str | None
    asked: dict[str, Any]


class _Walk:
    """A script taken one item at a time, against a clock."""

    def __init__(self, scene: conv.Scene, found: Ready, mixer: mix.Mixer, *, gap: float, kept: Path, scratch: Path,
                 voices: Voices, synthesizer: Callable[[str | None], Synthesizer], transcriber: Transcriber | None,
                 chosen: set[str] | None, before: dict[int, dict[str, Any]], said: Callable[[str], None],
                 backend: str | None, settings: Settings | None, how: dict[str, Any]):
        self.scene, self.found, self.mixer, self.gap = scene, found, mixer, gap
        self.kept, self.scratch, self.voices = kept, scratch, voices
        self.transcriber, self.chosen, self.before, self.said = transcriber, chosen, before, said
        self.backend, self.settings, self.how = backend, settings, how
        self.clock, self.number = 0.0, 0
        self.plays: list[_Play] = []
        self.stops: list[_Stop] = []
        self.marks: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.spoke: dict[str | None, Synthesizer] = {}
        self._synthesizer = synthesizer
        self._first = True

    def step(self, item: dict[str, Any]) -> None:
        kind = item["type"]
        if kind == "mark":
            self.marks.append({"t": round(self.clock, 3), "kind": item["kind"], **item["data"]})
        elif kind == "pause":
            self.clock += float(item["seconds"])
        elif kind in ("cue", "track"):
            self._sound(item)
        elif kind == "line":
            self._line(item)

    def _sound(self, item: dict[str, Any]) -> None:
        asked, track, action = item["overrides"], item["track"], item.get("action", "play")
        if action == "off":
            self.stops.append(_Stop(track, self.clock, item.get("sound"), asked))
            return
        at = _when(self.clock, asked)
        if action == "set":
            if asked.get("volume") is not None:
                self.mixer.move(track, at, float(asked["volume"]), float(asked.get("ramp") or 0.0))
            return
        play = self._play(track, item.get("sound"), item.get("file"), at, asked)
        if not asked.get("hold"):
            return
        held = self._held(play)
        if track == "video":
            written = play.file or (self.scene["sounds"].get(play.sound) or {}).get("file")
            where = self.found.files.get(written) if written else None
            self.marks.append({"t": round(at, 3), "kind": "video", "sound": play.name,
                               "file": str(where) if where else None,
                               "start": float(asked.get("start") or 0.0), "seconds": round(held, 3)})
        self.clock += held

    def _play(self, track: str, sound: str | None, file: str | None, at: float, asked: dict[str, Any]) -> _Play:
        self.plays.append(_Play(track, sound, file, at, asked, len(self.plays)))
        return self.plays[-1]

    def _held(self, play: _Play) -> float:
        """How long the scene waits for a sound that holds: as long as it was
        asked to last, else as long as the sound says it is, else its file's length."""
        spec = self.scene["sounds"].get(play.sound) or {}
        for given in (play.asked.get("duration"), play.asked.get("for"), spec.get("duration")):
            if given:
                return float(given)
        probe = self.found.probes.get(play.file or spec.get("file") or "")
        if probe is not None and probe.duration_s:
            return max(0.0, probe.duration_s - float(play.asked.get("start", spec.get("start")) or 0.0))
        return HELD

    def _line(self, item: dict[str, Any]) -> None:
        segments = item["segments"]
        text = _text(item)
        if not text:
            # Nothing to say: its sounds play where the clock stands, and its breaks are pauses.
            for piece in segments:
                if piece["type"] == "sfx":
                    self._play(_track_of(self.scene, piece["sound"]), piece["sound"], None, self.clock, piece["overrides"])
                elif piece["type"] == "break":
                    self.clock += float(piece["seconds"])
                elif piece["type"] == "mark":
                    self.marks.append({"t": round(self.clock, 3), "kind": piece["kind"], **piece["data"]})
            return
        self.number += 1
        number, speaker, asked = self.number, item["speaker"], item["overrides"]
        character = self.scene["characters"].get(speaker) or {}
        persona = _persona(self.scene, speaker)
        track = asked.get("track") or character.get("track") or "dialogue"
        file = self.kept / f"line_{number:03d}.wav"

        found = self._kept(number, persona, text, file)
        if found is None:
            found = self._say(item, number, persona, character, file)
            found.update(line=number, script_line=item["line"], speaker=speaker, persona=persona, text=text)
        sound = pcm.read(file, rate=self.mixer.rate)
        # An echo or a room goes on after the words do, and rings under whatever comes next.
        seconds = max(0.0, len(sound) / self.mixer.rate - float(found.get("rings") or 0.0))
        self.mixer.place(track, sound, self.clock)
        if track != "dialogue":
            self.mixer.speech.add(track)
        for piece in segments:
            if piece["type"] == "sfx":
                self._play(_track_of(self.scene, piece["sound"]), piece["sound"], None,
                           self.clock + seconds * _part(segments, piece), piece["overrides"])
        self.marks.append({"t": round(self.clock, 3), "kind": "line", "line": number, "speaker": speaker,
                           "persona": persona, "track": track, "seconds": round(seconds, 3), "text": text,
                           **{key: character[key] for key in ("name", "title") if character.get(key)}})
        # A mark written inside the line falls where the words have reached.
        for piece in segments:
            if piece["type"] == "mark":
                self.marks.append({"t": round(self.clock + seconds * _part(segments, piece), 3),
                                   "kind": piece["kind"], **piece["data"]})
        self.records.append(dict(found, file=str(file.resolve()), track=track, at=round(self.clock, 3),
                                 seconds=round(seconds, 3)))
        self.clock += seconds + self.gap

    def _kept(self, number: int, persona: str, text: str, file: Path) -> dict[str, Any] | None:
        """The line as it was said last time, when this render is not for it and nothing about it has changed."""
        if self.chosen is None or persona in self.chosen or str(number) in self.chosen:
            return None
        old = self.before.get(number)
        if old is None or not file.is_file() or old.get("persona") != persona or old.get("text") != text:
            return None
        self.said(f"  [{number:03d}] {persona}: kept as it was")
        return dict(old, reused=True)

    def _say(self, item: dict[str, Any], number: int, persona: str, character: dict[str, Any], file: Path) -> dict[str, Any]:
        asked = item["overrides"]
        if self.transcriber is None:
            self.transcriber = transcription_adapter(settings=self.settings)
        said = lines.perform(
            item["segments"], self.scratch, persona=persona, voices=self.voices, synthesizer=self._speaker,
            transcriber=self.transcriber, seed=FIRST_SEED + number * SEED_STEP, name=f"line_{number:03d}",
            emotion=asked.get("emotion", character.get("emotion")), line_seed=asked.get("seed"),
            backend=self.backend, settings=self.settings, **self.how,
        )
        take, verdict, status = said.take, said.take.verdict, said.take.status
        if self._first and verdict.reason == lines.ERROR:
            # Every try at the first line failed outright. That is the setup, and every other line would go the same way.
            raise SpeechError(f"the first line could not be said: {verdict.heard}")
        self._first = False
        wanted = {key: value for key, value in {**character, **asked}.items() if key in _EFFECT_KEYS}
        effects = Effects.from_overrides(wanted)
        rings = 0.0
        if take.path is None:
            import numpy as np

            pcm.write(file, np.zeros(int(QUIET * lines.QUIET_RATE), dtype=np.float32), lines.QUIET_RATE)
            status = lines.NEEDS_REVIEW
        elif effects and status != lines.NEEDS_REVIEW:
            effects_slot.apply(take.path, file, effects, backend=self.backend, settings=self.settings)
            rings = max(0.0, (pcm.seconds(file) or 0.0) - (pcm.seconds(take.path) or 0.0))
        else:
            shutil.copyfile(take.path, file)
        applied = bool(effects) and take.path is not None and status != lines.NEEDS_REVIEW
        flag = f"  needs a listen ({verdict.reason}, heard {verdict.heard[:40]!r})" if status == lines.NEEDS_REVIEW else ""
        self.said(f"  [{number:03d}] {persona}/{said.voice.emotion} seed {take.seed}, "
                  f"{take.attempts} take{'' if take.attempts == 1 else 's'}, {status}{flag}")
        return {
            "emotion": said.voice.emotion, "seed": take.seed, "status": status, "attempts": take.attempts,
            "word_error": round(verdict.word_error, 3), "similarity": round(verdict.similarity, 3),
            "reason": verdict.reason, "heard": verdict.heard,
            "spoken": take.spoken if take.spoken != said.text else None,
            "effects": (wanted or None) if applied else None,
            "piece_effects": list(said.effects) or None, "rings": round(rings, 3),
            "repeat": {"first": round(take.repeat.first_s, 3), "second": round(take.repeat.second_s, 3),
                       "similarity": round(take.repeat.similarity, 3)} if take.repeat else None,
        }

    def _speaker(self, engine: str | None) -> Synthesizer:
        if engine not in self.spoke:
            self.spoke[engine] = self._synthesizer(engine)
        return self.spoke[engine]


def _place(mixer: mix.Mixer, plays: list[_Play], stops: list[_Stop], bank: dict[str, dict[str, Any]],
           found: Ready, total: float) -> tuple[list[dict[str, Any]], list[str]]:
    """Put every sound on its track, each as long as the script makes it.

    Returns what was placed, and the names of the sounds that had no file.
    """
    placed: list[dict[str, Any]] = []
    missing: list[str] = []
    rate = mixer.rate
    for play in plays:
        spec = bank.get(play.sound) or {}
        written = play.file or spec.get("file")
        path = found.files.get(written) if written else None
        if path is None:
            missing.append(play.name)
            continue
        if not found.probes[written].has_audio:
            continue   # a clip with a picture and no sound
        track, asked = mixer.track(play.track), play.asked
        sound = pcm.read(path, rate=rate)
        skipped = float(asked.get("start", spec.get("start")) or 0.0)
        if skipped > 0:
            sound = sound[int(skipped * rate):]
        level = asked.get("level", spec.get("level", track.level))
        if level is not None and len(sound):
            sound = mix.level(sound, float(level))
        loop = False if asked.get("noloop") else bool(asked["loop"] if "loop" in asked else spec.get("loop", track.loop))

        own = len(sound) / rate
        given = asked.get("duration") or spec.get("duration")
        # A bed that loops has no end of its own. A one-shot is as long as it says it is, or as its file.
        length = (None if loop else own) if track.bed else (float(given) if given else own)
        fade_out = float(asked.get("fade_out") or 0.0)
        cut, fade_after = _ends(play, plays, stops, total, track)
        lasting = asked.get("for")
        if lasting is not None and (length is None or float(lasting) < length):
            length, fade_out = float(lasting), float(asked.get("fade_out", track.fade_out) or 0.0)
        if cut is not None and (length is None or cut < length):
            length, fade_out = cut + fade_after, fade_after
        if length is None:
            length = max(0.0, total - play.start)

        if "fade_in" in asked:
            fade_in = float(asked["fade_in"] or 0.0)
        else:
            fade_in = track.fade_in
            if skipped > 0:
                fade_in = max(fade_in, EASED_IN)   # music joined partway through is never started cold
            if (track.bed or track.exclusive) and any(
                    other is not play and other.track == play.track and other.start < play.start for other in plays):
                # One taking over from another: it swells in while the other eases out.
                fade_in = max(fade_in, track.fade_out)

        sound = mix.fit(sound, length, rate, loop=loop, xfade=float(asked.get("xfade", track.xfade) or 0.0))
        sound = mix.fade(sound, rate, fade_in=fade_in, fade_out=fade_out)
        mixer.place(play.track, sound, play.start, pan=float(asked.get("pan") or 0.0),
                    volume=float(asked.get("volume", 1.0)))
        placed.append({"sound": play.name, "track": play.track, "at": round(play.start, 3),
                       "seconds": round(length, 3), "fade_in": round(fade_in, 3), "fade_out": round(fade_out, 3)})
    return placed, sorted(set(missing))


def _ends(play: _Play, plays: list[_Play], stops: list[_Stop], total: float,
          track: mix.Track) -> tuple[float | None, float]:
    """When something cuts a sound short, counted from its start, and the fade it goes out with.

    What cuts it is the first of: an `off` on its track, for it or for
    everything there; and, on a bed or a track that plays one thing at a
    time, the next sound there that takes its place. A bed that nothing cuts
    plays to the end of the scene and the track's tail past it.
    """
    own = float(play.asked.get("fade_out", track.fade_out) or 0.0)
    cuts: list[tuple[float, float]] = []
    for stop in stops:
        if stop.track == play.track and stop.at >= play.start and stop.sound in (None, play.name):
            cuts.append((stop.at, float(stop.asked.get("fade_out", track.fade_out) or 0.0)))
    if track.bed or track.exclusive:
        for other in plays:
            if other is play or other.track != play.track:
                continue
            after = other.start > play.start or (other.start == play.start and other.order > play.order)
            if after and (track.exclusive or other.name == play.name):
                cuts.append((other.start, track.fade_out))
    if cuts:
        at, fade = min(cuts, key=lambda cut: cut[0])
        return max(0.0, at - play.start), fade
    if track.bed:
        return max(0.0, total + track.tail - play.start - own), own
    return None, own


def _played(scene: conv.Scene) -> Iterator[tuple[int, str, str | None, str | None]]:
    """Every sound a script plays: the line, the track, and its name or its file."""
    for item in scene["items"]:
        if item["type"] == "cue" or (item["type"] == "track" and item.get("action") == "play"):
            yield item["line"], item["track"], item.get("sound"), item.get("file")
        elif item["type"] == "line":
            for piece in item["segments"]:
                if piece["type"] == "sfx":
                    yield item["line"], _track_of(scene, piece["sound"]), piece["sound"], None


def _track_of(scene: conv.Scene, sound: str) -> str:
    return (scene["sounds"].get(sound) or {}).get("track") or "foreground"


def _persona(scene: conv.Scene, speaker: str) -> str:
    return (scene["characters"].get(speaker) or {}).get("persona") or speaker.lower()


def _text(item: dict[str, Any]) -> str:
    return " ".join(piece["text"] for piece in lines.joined(item["segments"]) if piece["type"] == "text").strip()


def _when(clock: float, asked: dict[str, Any]) -> float:
    """Where a sound starts: at a time of its own, some seconds from the clock, or on it."""
    at = asked.get("at")
    if not at:
        return clock
    if at["mode"] == "absolute":
        return max(0.0, float(at["seconds"]))
    return max(0.0, clock + float(at["seconds"]))


def _part(segments: Sequence[dict[str, Any]], sound: dict[str, Any]) -> float:
    """How far through a line's words a sound or a mark written inside it comes, from 0 to 1."""
    before = total = 0
    passed = False
    for piece in segments:
        passed = passed or piece is sound
        if piece["type"] == "text":
            total += len(piece["text"])
            before += 0 if passed else len(piece["text"])
    return min(1.0, before / total) if total else 0.0


def _only(only: str | Iterable[Any] | None, personas: set[str], count: int) -> set[str] | None:
    if only is None:
        return None
    asked = {str(one).strip() for one in (only.split(",") if isinstance(only, str) else only)} - {""}
    for one in sorted(asked):
        if one not in personas and not (one.isdigit() and 1 <= int(one) <= count):
            raise SpeechError(f"{one!r} is neither somebody who speaks in this script ({', '.join(sorted(personas))}) "
                              f"nor one of its {count} lines")
    return asked or None


def _writable(target: Path) -> None:
    """Find out now, not after an hour of speech, that this kind of file cannot be written."""
    kind = target.suffix.lower()
    if not kind:
        raise SpeechError(f"{target.name!r} does not say what kind of file to write; end it in .flac, .wav or another")
    with tempfile.TemporaryDirectory(prefix="scene-") as folder:
        try:
            ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                       "-i", f"anullsrc=r={mix.RATE}:cl=stereo", "-t", "0.1", *_CODECS.get(kind, ()),
                       Path(folder) / f"try{kind}")
        except FfmpegError as error:
            if "was not found" in str(error):
                raise
            raise SpeechError(f"the ffmpeg here cannot write {kind} files; end the output in .flac or .wav") from error


def _metered(printed: str) -> dict[str, float] | None:
    """What the loudness meter said at its end; None when there was nothing to meter."""
    summary = printed[printed.rfind("Summary:"):] if "Summary:" in printed else ""
    found: dict[str, float] = {}
    for key, name, unit in (("loudness", "I", "LUFS"), ("threshold", "Threshold", "LUFS"),
                            ("range", "LRA", "LU"), ("peak", "Peak", "dBFS")):
        said = re.search(rf"^\s*{name}:\s+(\S+) {unit}\s*$", summary, re.MULTILINE)
        value = _finite(said.group(1)) if said else None
        if value is None:
            return None
        found[key] = value
    # The meter's floor: nothing in the mix was loud enough to count.
    return found if found["loudness"] > -69.9 else None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
