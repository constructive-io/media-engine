"""Libraries: voices, sound banks and faces kept in a repository of their own.

A script names a persona and a bank and nothing else: `persona: ada`,
`@include weather`. Whose voice `ada` is, and which file `weather` is, are
answered by this machine. A library is how the answers are passed around. It
is a folder, most often a git repository, with one file at its top that says
what it holds:

    media-library.json
    {
      "library": "lighthouse",
      "version": "0.1.0",
      "license": "CC-BY-4.0",
      "voices": [
        {"persona": "ada", "name": "Ada Marsh", "emotion": "neutral",
         "reference": "voices/ada/neutral.flac",
         "consent": {"by": "Ada Marsh", "kind": "own", "on": "2026-11-26"}}
      ],
      "banks":  ["banks"],
      "sounds": ["sounds"],
      "faces":  {"ada": "faces/ada.png"},
      "personas": {"ada": {"name": "Ada Marsh", "kind": "person",
                           "bio": "Keeps the light. Dry, exact, fond of weather."}}
    }

A voice is a clip to clone from and the words said in it: `said` in the
manifest, or a file beside the clip with the same name ending `.txt`. A voice
may instead be only a `profile`, the name a speech server knows one of its own
voices by. Either way it says whose it is and by what right it is here:
`consent` is asked of every voice, and a library with a voice that has none is
not a library. `kind` is one of `KINDS`.

`banks` are folders of `.sounds` files, `sounds` the folders the files those
name are in, and `faces` a picture for a persona. `personas` says who each
one is: a name, whether they are a person or a character, and a few words
about them, which is what a writer is told when a script is written for them.

A library is added to this machine once, by its address, and is kept at the
commit it was added at until it is updated. What was added is written in
`libraries.json` under the engine's home. A project says which libraries it
is made with in a `media-libraries.json` of its own, which `lock` writes and
`install` reads, so that everyone who renders it has the same ones.

Nothing in a library is run. Its manifest is read, and the paths it names
must be inside it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .settings import Settings

MANIFEST = "media-library.json"
LOCK = "media-libraries.json"

#: By what right a voice is in a library.
KINDS = {
    "own": "the voice is the own voice of whoever recorded it, who is named in `by`",
    "permission": "the person whose voice it is, named in `by`, gave leave for it to be used this way",
    "licensed": "the recording came under a license that allows this; `note` says which and from where",
    "synthetic": "nobody's voice: it was designed or generated, as `note` says how",
}

SOUND = (".flac", ".wav", ".mp3", ".m4a", ".ogg", ".opus", ".aiff")
PICTURE = (".png", ".jpg", ".jpeg", ".webp")
#: A clip to clone from is half a minute at most; a file past this is likely the recording it was cut from.
CLIP_BYTES = 6_000_000

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SHORT = re.compile(r"[\w.-]+/[\w.-]+")
#: An address may come from somebody else's file, a project's or an index's. git is let fetch over the
#: transports that only fetch, and over none that runs a program (`ext::`, or a helper named in the address).
_FETCHING = ("-c", "protocol.allow=never", *(part for transport in ("file", "git", "http", "https", "ssh")
                                            for part in ("-c", f"protocol.{transport}.allow=always")))
_KNOWN = {"library", "version", "license", "summary", "homepage", "voices", "banks", "sounds", "faces", "personas"}
_OF_A_PERSONA = {"name", "kind", "bio"}
#: What a persona is, as the engine keeps them.
PERSONA_KINDS = ("person", "character")
_OF_A_VOICE = {"persona", "name", "emotion", "reference", "said", "profile", "engine", "seed", "consent", "tags"}


class LibraryError(Exception):
    """A library that cannot be read, found, added or used."""


@dataclass(frozen=True)
class Voice:
    persona: str
    emotion: str
    name: str | None
    reference: Path | None
    said: str | None
    profile: str | None
    engine: str | None
    seed: int | None
    consent: dict[str, Any]


@dataclass(frozen=True)
class About:
    """Who a persona is, as a library says it."""

    persona: str
    name: str | None = None
    kind: str | None = None
    bio: str | None = None


@dataclass(frozen=True)
class Library:
    name: str
    folder: Path
    version: str | None
    license: str | None
    summary: str | None
    voices: tuple[Voice, ...]
    banks: tuple[Path, ...]
    sounds: tuple[Path, ...]
    faces: dict[str, Path]
    personas: dict[str, About] = field(default_factory=dict)


@dataclass(frozen=True)
class Checked:
    library: Library | None
    problems: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Added:
    name: str
    source: str
    folder: Path
    commit: str | None
    linked: bool
    added: str


def check(folder: str | Path, *, measure: bool = True) -> Checked:
    """Read a library and say everything wrong with it.

    `problems` stop it being a library; `notes` are worth knowing. With
    `measure` every clip is measured, when ffmpeg is here to do it.
    """
    folder = Path(folder).expanduser()
    problems: list[str] = []
    notes: list[str] = []
    written = folder / MANIFEST
    if not written.is_file():
        return Checked(None, (f"{folder} is not a library: it has no {MANIFEST}",), ())
    try:
        manifest = json.loads(written.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return Checked(None, (f"{written} cannot be read as JSON: {error}",), ())
    if not isinstance(manifest, dict):
        return Checked(None, (f"{written} should hold an object, in braces",), ())
    folder = folder.resolve()

    name = manifest.get("library")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        problems.append('"library" is the library\'s name: lower case letters and numbers, joined by hyphens')
        name = None
    for key in sorted(set(manifest) - _KNOWN):
        notes.append(f'"{key}" is not something a library says; it is ignored')

    def inside(given: Any, what: str) -> Path | None:
        if not isinstance(given, str) or not given.strip():
            problems.append(f"{what} should be a path inside the library")
            return None
        path = (folder / given).resolve()
        if Path(given).is_absolute() or not path.is_relative_to(folder):
            problems.append(f"{what} is `{given}`, which is outside the library: everything it names is inside it")
            return None
        return path

    voices: list[Voice] = []
    seen: set[tuple[str, str]] = set()
    listed = manifest.get("voices", [])
    if not isinstance(listed, list):
        problems.append('"voices" should be a list')
        listed = []
    for number, given in enumerate(listed, start=1):
        if not isinstance(given, dict):
            problems.append(f"voice {number} should be an object, in braces")
            continue
        persona, emotion = given.get("persona"), given.get("emotion", "neutral")
        who = f"voice {number}"
        if not isinstance(persona, str) or not _NAME.fullmatch(persona):
            problems.append(f'{who}: "persona" is who speaks with it, as a script writes them: lower case, with hyphens')
            persona = None
        if not isinstance(emotion, str) or not _NAME.fullmatch(emotion):
            problems.append(f'{who}: "emotion" should be one word, such as neutral or whisper')
            emotion = "neutral"
        if persona:
            who = f"{persona}/{emotion}"
            if (persona, emotion) in seen:
                problems.append(f"{who} is there twice: a persona has one voice for an emotion")
            seen.add((persona, emotion))
        for key in sorted(set(given) - _OF_A_VOICE):
            notes.append(f'{who}: "{key}" is not something a voice says; it is ignored')

        reference, said = None, given.get("said")
        if said is not None and (not isinstance(said, str) or not said.strip()):
            problems.append(f'{who}: "said" should be the words said in the clip')
            said = None
        profile = given.get("profile")
        if profile is not None and (not isinstance(profile, str) or not profile.strip()):
            problems.append(f'{who}: "profile" should be the name a speech server knows the voice by')
            profile = None
        if "reference" in given:
            reference = inside(given["reference"], f'{who}: "reference"')
            if reference is not None and not reference.is_file():
                problems.append(f"{who}: its clip, `{given['reference']}`, is not there")
                reference = None
            elif reference is not None and reference.suffix.lower() not in SOUND:
                problems.append(f"{who}: its clip, `{given['reference']}`, is not a kind of sound file known here "
                                f"({', '.join(SOUND)})")
                reference = None
        elif not profile:
            problems.append(f'{who}: a voice is a "reference", a clip to clone from, or a "profile", the name a '
                            "speech server knows one of its own voices by")
        if reference is not None:
            beside = reference.with_suffix(".txt")
            if said is None and beside.is_file():
                said = beside.read_text(encoding="utf-8", errors="replace").strip() or None
            if said is None:
                problems.append(f'{who}: the words said in its clip are not written down. Give them as "said", or '
                                f"in `{beside.relative_to(folder)}`")
            if reference.stat().st_size > CLIP_BYTES:
                notes.append(f"{who}: its clip is {reference.stat().st_size / 1e6:.0f} MB. A clip to clone from is "
                             "half a minute at most; the recording it was cut from is kept somewhere else")
            if measure:
                _measured(reference, who, problems, notes)

        seed = given.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            problems.append(f'{who}: "seed" should be a whole number')
            seed = None
        engine = given.get("engine")
        if engine is not None and not isinstance(engine, str):
            problems.append(f'{who}: "engine" should be the name of a speech adapter')
            engine = None
        consent = given.get("consent")
        if not isinstance(consent, dict):
            problems.append(f'{who}: it has no "consent". Every voice says whose it is and by what right it is '
                            f'here: {{"by": "who", "kind": "{" | ".join(KINDS)}"}}')
            consent = {}
        else:
            if not isinstance(consent.get("by"), str) or not consent["by"].strip():
                problems.append(f'{who}: its consent does not say who gave it, as "by"')
            if consent.get("kind") not in KINDS:
                problems.append(f'{who}: the "kind" of its consent should be one of {", ".join(KINDS)}')
            elif consent["kind"] in ("licensed", "synthetic") and not str(consent.get("note") or "").strip():
                problems.append(f'{who}: consent of the kind `{consent["kind"]}` says more in a "note": '
                                + ("which license, and where the recording is from" if consent["kind"] == "licensed"
                                   else "how the voice was made"))
            if "on" in consent:
                try:
                    date.fromisoformat(str(consent["on"]))
                except ValueError:
                    problems.append(f'{who}: the day its consent was given, "on", should be written like 2026-11-26')
        if persona:
            voices.append(Voice(persona, emotion, given.get("name") if isinstance(given.get("name"), str) else None,
                                reference, said.strip() if said else None, profile, engine, seed, consent))

    def folders(key: str) -> tuple[Path, ...]:
        given = manifest.get(key, [])
        if not isinstance(given, list):
            problems.append(f'"{key}" should be a list of folders')
            return ()
        found = []
        for one in given:
            path = inside(one, f'"{key}"')
            if path is not None and not path.is_dir():
                problems.append(f'"{key}" names `{one}`, and there is no such folder')
            elif path is not None:
                found.append(path)
        return tuple(found)

    banks, sounds = folders("banks"), folders("sounds")
    _banks(banks, sounds, folder, problems, notes)

    faces: dict[str, Path] = {}
    given = manifest.get("faces", {})
    if not isinstance(given, dict):
        problems.append('"faces" should be an object: a picture for a persona')
        given = {}
    for persona, picture in given.items():
        path = inside(picture, f'"faces": {persona}')
        if path is None:
            continue
        if not path.is_file():
            problems.append(f"the face of {persona}, `{picture}`, is not there")
        elif path.suffix.lower() not in PICTURE:
            problems.append(f"the face of {persona}, `{picture}`, should be a picture ({', '.join(PICTURE)})")
        else:
            faces[persona] = path

    personas: dict[str, About] = {}
    given = manifest.get("personas", {})
    if not isinstance(given, dict):
        problems.append('"personas" should be an object: who each persona is')
        given = {}
    for persona, about in given.items():
        if not _NAME.fullmatch(persona):
            problems.append(f'"personas": `{persona}` should be a persona\'s slug: lower case letters and numbers, '
                            "joined by hyphens")
            continue
        if not isinstance(about, dict):
            problems.append(f'"personas": {persona} should be an object, with any of {", ".join(sorted(_OF_A_PERSONA))}')
            continue
        for key in sorted(set(about) - _OF_A_PERSONA):
            notes.append(f'"personas": {persona} has "{key}", which is not something said of a persona; it is ignored')
        kind = about.get("kind")
        if kind is not None and kind not in PERSONA_KINDS:
            problems.append(f'"personas": {persona} is a {" or a ".join(PERSONA_KINDS)}, not {kind!r}')
            continue
        wrong = [key for key in ("name", "bio") if about.get(key) is not None and not isinstance(about[key], str)]
        if wrong:
            problems.append(f'"personas": {persona} should have words for {" and ".join(wrong)}')
            continue
        personas[persona] = About(persona, _text(about.get("name")), kind, _text(about.get("bio")))

    if not voices and not banks and not faces and not problems:
        notes.append("it holds no voices, no banks and no faces yet")
    library = None if problems or name is None else Library(
        name, folder, _text(manifest.get("version")), _text(manifest.get("license")), _text(manifest.get("summary")),
        tuple(voices), banks, sounds, faces, personas)
    return Checked(library, tuple(problems), tuple(notes))


def load(folder: str | Path) -> Library:
    """A library that is sound, or a LibraryError that says everything wrong with it."""
    checked = check(folder, measure=False)
    if checked.library is None:
        raise LibraryError(f"{folder} cannot be used as a library:\n  " + "\n  ".join(checked.problems))
    return checked.library


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


# Putting a voice in.

#: A library's README keeps a table of its voices between these two lines, when it has them.
TABLE = ("<!-- voices -->", "<!-- /voices -->")


def clip(
    folder: str | Path, persona: str, recording: str | Path, *, by: str, kind: str, on: str | None = None,
    note: str | None = None, name: str | None = None, emotion: str = "neutral", start: float | None = None,
    end: float | None = None, said: str | None = None, transcriber: Any = None, language: str = "en",
    tighten: float | None = None, cleaning: bool = True, settings: Settings | None = None,
) -> dict[str, Any]:
    """Put a voice into the library in `folder`, from a recording of it.

    The recording is cut into a clip, `voices/<persona>/<emotion>.flac`:
    from `start` to `end`, or where `voice.cuts` thinks best when it is too
    long to be one and neither is given. With `cleaning`, the stretch is
    measured for noise first and cleaned when that is worth doing and does
    the voice no harm. Its words are `said`, or what `transcriber` hears,
    and are written beside it.

    Then the voice is written into the manifest, in place of one for the
    same persona and mood, with its consent: whose voice it is (`by`) and
    by what right it is here (`kind`, one of `KINDS`). Neither has a
    default and neither is guessed, since only the person who keeps the
    library can say. The README's table of voices is written again when it
    has one. The recording itself is neither copied nor kept.

    A LibraryError when the folder is no library or the consent does not
    say enough; a SpeechError when no clip can be cut. Then nothing is
    changed.
    """
    from . import ffmpeg
    from .audio import clean as cleaner
    from .audio import voice

    folder, recording = Path(folder).expanduser(), Path(recording).expanduser()
    written = folder / MANIFEST
    if not written.is_file():
        raise LibraryError(f"{folder} is not a library: it has no {MANIFEST}")
    try:
        manifest = json.loads(written.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LibraryError(f"{written} cannot be read as JSON: {error}") from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("voices", []), list):
        raise LibraryError(f'{written} should hold an object with a list of "voices"')
    for what, value in (("persona", persona), ("mood", emotion)):
        if not _NAME.fullmatch(value):
            raise LibraryError(f"a {what} is lower case letters and numbers, joined by hyphens: not `{value}`")
    consent = _consent(by, kind, on, note)

    notes: list[str] = []
    cleaned = None
    target = folder / "voices" / persona / f"{emotion}.flac"
    with tempfile.TemporaryDirectory(prefix="clip-") as scratch:
        source, chosen = recording, None
        whole = voice.measure(recording)["seconds"]
        if start is None and end is None and whole > voice.MOST_S:
            chosen = next(iter(voice.cuts(recording, most=1)), None)
            if chosen is None:
                raise LibraryError(
                    f"{recording} runs {whole:.1f} seconds, and nowhere in it do {voice.GOOD_S[0]:.0f} to "
                    f"{voice.GOOD_S[1]:.0f} seconds of talk begin after a quiet and end in one. Say where to cut "
                    "it with --from and --to")
            start, end = chosen["from"], chosen["to"]
        if start is not None and end is not None and end <= start:
            raise LibraryError("--to must be later than --from")
        if start is not None or end is not None:
            source = Path(scratch) / "stretch.wav"
            ffmpeg.run(ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                       *(("-ss", f"{start:g}") if start is not None else ()),
                       *(("-to", f"{end:g}") if end is not None else ()),
                       "-i", recording, "-vn", "-c:a", "pcm_s24le", source)
        if cleaning:
            try:
                told = cleaner.clean(source, Path(scratch) / "cleaned.wav", settings=settings)
            except Exception as error:   # no denoiser on this machine, or one that failed: the clip is cut all the same
                notes.append(f"it was not cleaned: {error}")
            else:
                if told["output"] and told["damaged"]:
                    notes.append(f"cleaning it took {told['voice_lost']:.1f} dB off the voice, so it was left as "
                                 "it was recorded. Record it again somewhere quieter if the clip is heard to hiss")
                elif told["output"]:
                    source = Path(told["output"])
                    cleaned = {"steps": told["steps"], "why": told["why"], "gained": told["gained"],
                               "voice_lost": told["voice_lost"]}
        made = voice.reference(source, Path(scratch) / target.name, said=said, transcriber=transcriber,
                               language=language, tighten=tighten)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(made["output"], target)
        words = target.with_suffix(".txt")
        if made["said"]:
            words.write_text(made["said"] + "\n", encoding="utf-8")
        elif words.is_file():
            words.unlink()             # the words of the clip that was here before are not the words of this one
    notes.extend(made["notes"])
    if not made["said"]:
        notes.append(f"its words are not written down. Write exactly what is said in the clip in "
                     f"{words.relative_to(folder)}: the library is not sound until they are")

    reference = target.relative_to(folder).as_posix()
    voices = manifest.setdefault("voices", [])
    same = [one for one in voices if isinstance(one, dict) and one.get("persona") == persona
            and (one.get("emotion") or "neutral") == emotion]
    entry = {"persona": persona, "name": name or (same[0].get("name") if same else None) or by.strip(),
             "emotion": emotion, "reference": reference,
             **{key: same[0][key] for key in ("seed", "tags") if same and key in same[0]}, "consent": consent}
    if same:
        voices[voices.index(same[0])] = entry
        for more in same[1:]:
            voices.remove(more)
    else:
        voices.append(entry)
    written.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    checked = check(folder)
    return {"library": manifest.get("library"), "persona": persona, "emotion": emotion, "clip": reference,
            "words": words.relative_to(folder).as_posix() if made["said"] else None, "said": made["said"],
            "heard": made["heard"], "recording": str(recording), "from": start, "to": end,
            "chosen": chosen, "cleaned": cleaned, "is": made["is"], "notes": notes, "replaced": bool(same),
            "consent": consent, "readme": tabled(folder), "problems": list(checked.problems)}


def _consent(by: str, kind: str, on: str | None, note: str | None) -> dict[str, str]:
    """Consent as a manifest writes it down, or a LibraryError that says what it lacks."""
    if not isinstance(by, str) or not by.strip():
        raise LibraryError("say whose voice it is, with --by. Nobody but the person who keeps the library can")
    if kind not in KINDS:
        raise LibraryError("say by what right the voice is here, with --kind:\n  "
                           + "\n  ".join(f"{one}: {means}" for one, means in KINDS.items()))
    if kind in ("licensed", "synthetic") and not (note or "").strip():
        raise LibraryError(f"consent of the kind `{kind}` says more with --note: "
                           + ("which license, and where the recording is from" if kind == "licensed"
                              else "how the voice was made"))
    consent = {"by": by.strip(), "kind": kind}
    if on is not None:
        try:
            date.fromisoformat(on)
        except ValueError:
            raise LibraryError("the day consent was given, --on, is written like 2026-11-26") from None
        consent["on"] = on
    if (note or "").strip():
        consent["note"] = note.strip()
    return consent


def table(folder: str | Path) -> str:
    """The voices of the library in `folder` as a table for its README: who, in what mood, whose voice, by what right."""
    try:
        voices = json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8")).get("voices", [])
    except (OSError, ValueError, AttributeError):
        voices = []

    def cell(value: Any) -> str:
        return " ".join(str(value or "").split()).replace("|", "\\|")

    rows = []
    for one in voices if isinstance(voices, list) else []:
        if not isinstance(one, dict):
            continue
        consent = one.get("consent") if isinstance(one.get("consent"), dict) else {}
        right = ", ".join(str(part) for part in (consent.get("kind"), consent.get("on")) if part)
        if consent.get("note"):
            right += f": {consent['note']}"
        rows.append(f"| `{cell(one.get('persona'))}` | {cell(one.get('emotion') or 'neutral')} | "
                    f"{cell(consent.get('by') or one.get('name'))} | {cell(right)} |")
    return "\n".join(["| Persona | Mood | Whose voice | By what right |", "| --- | --- | --- | --- |",
                      *(rows or ["| *none yet* | | | |"])])


def tabled(folder: str | Path) -> bool:
    """Write the table of voices into the library's README, between the lines of `TABLE`. Whether there was a place for it."""
    readme = Path(folder) / "README.md"
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return False
    first, last = text.find(TABLE[0]), text.find(TABLE[1])
    if first < 0 or last < first:
        return False
    readme.write_text(text[:first + len(TABLE[0])] + "\n" + table(folder) + "\n" + text[last:], encoding="utf-8")
    return True


def _measured(clip: Path, who: str, problems: list[str], notes: list[str]) -> None:
    from . import ffmpeg

    try:
        from .audio import voice

        measured = voice.measure(clip)
    except (ModuleNotFoundError, ffmpeg.FfmpegError):
        return   # nothing here can measure: what can be read without it has been
    except Exception as error:   # a SpeechError, which says what is wrong with the clip
        problems.append(f"{who}: {error}")
        return
    stops, more = voice.fit(measured)
    problems.extend(f"{who}: {said}" for said in stops)
    notes.extend(f"{who}: {said}" for said in more)


def _banks(banks: Sequence[Path], sounds: Sequence[Path], folder: Path, problems: list[str],
           notes: list[str]) -> None:
    from . import conv

    finder = conv.Files([str(one) for one in banks])
    names = finder.names()
    if banks and not names:
        notes.append(f"no bank is in {', '.join(str(one.relative_to(folder)) for one in banks)}: "
                     f"a bank is a file ending {conv.banks.ENDING}")
    for name in names:
        try:
            scene = conv.parse(f"@include {name}\n---\n", source=str(folder / MANIFEST), banks=finder)
        except conv.ConvError as error:
            problems.extend(f"bank `{name}`: {problem['message']}" for problem in error.problems)
            continue
        for sound, spec in scene["sounds"].items():
            file = spec.get("file")
            if not file:
                continue
            if Path(file).expanduser().is_absolute():
                problems.append(f"bank `{name}`: `{sound}` plays `{file}`, a path only this machine has")
            elif not any((one / file).is_file() for one in sounds):
                where = ", ".join(str(one.relative_to(folder)) for one in sounds) or 'no folder: "sounds" names none'
                problems.append(f"bank `{name}`: `{sound}` plays `{file}`, which is not in the library's sounds "
                                f"({where})")


# What has been added to this machine.

def added(settings: Settings | None = None) -> dict[str, Added]:
    """Every library added to this machine, by name."""
    home = (settings or Settings.from_env()).home
    try:
        kept = json.loads((home / "libraries.json").read_text(encoding="utf-8"))["libraries"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {name: Added(name, one["source"], Path(one["folder"]), one.get("commit"), bool(one.get("linked")),
                        one.get("added") or "")
            for name, one in sorted(kept.items()) if isinstance(one, dict) and "folder" in one and "source" in one}


def _write(libraries: dict[str, Added], settings: Settings | None) -> None:
    home = (settings or Settings.from_env()).home
    home.mkdir(parents=True, exist_ok=True)
    kept = {name: {"source": one.source, "folder": str(one.folder), "commit": one.commit, "linked": one.linked,
                   "added": one.added} for name, one in sorted(libraries.items())}
    scratch = home / "libraries.json.new"
    scratch.write_text(json.dumps({"libraries": kept}, indent=2) + "\n", encoding="utf-8")
    scratch.replace(home / "libraries.json")


def add(source: str, *, ref: str | None = None, settings: Settings | None = None) -> tuple[Added, Checked]:
    """Add a library to this machine, from a folder or from the address of a git repository.

    A folder is used where it is, so that a library being written can be
    tried. An address is cloned under the engine's home and kept at `ref`,
    or at the commit its main branch was at. `owner/name` is a repository on
    GitHub, and a bare name is looked for in the indexes this machine reads.
    A library is read before it is added, and one with problems is not added.
    """
    settings = settings or Settings.from_env()
    libraries = added(settings)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    local = Path(source).expanduser()
    if local.is_dir():
        if ref is not None:
            raise LibraryError("a folder is used as it stands: check out the commit you want in it")
        checked = check(local, measure=False)
        if checked.library is None:
            raise LibraryError(f"{source} was not added:\n  " + "\n  ".join(checked.problems))
        name = checked.library.name
        if name in libraries and libraries[name].folder != checked.library.folder:
            raise LibraryError(f"a library called `{name}` was added already, from {libraries[name].source}. "
                               f"Remove that one first: media library remove {name}")
        one = Added(name, str(checked.library.folder), checked.library.folder, _commit(checked.library.folder),
                    True, now)
    else:
        named = None
        if _NAME.fullmatch(source):
            # A bare name is looked for in the indexes, which say where it is.
            from . import indexes

            unreached: list[str] = []
            named = indexes.find(source, settings, tell=unreached.append)
            if named is None:
                raise LibraryError("\n  ".join((
                    f"no index lists a library called `{source}`. Give a folder or the address of a repository, "
                    "or see what is listed with: media library search", *unreached)))
            source = named.source
        address = f"https://github.com/{source}.git" if _SHORT.fullmatch(source) else source
        if address.startswith("-"):
            raise LibraryError(f"{source} is not the address of a repository")
        kept = settings.home / "libraries"
        kept.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=".adding-", dir=kept))
        try:
            _git("clone", "--quiet", "--no-recurse-submodules", "--", address, str(scratch / "clone"))
            if ref is not None:
                _git("checkout", "--quiet", "--detach", ref, cwd=scratch / "clone")
            checked = check(scratch / "clone", measure=False)
            if checked.library is None:
                raise LibraryError(f"{source} was not added:\n  " + "\n  ".join(checked.problems))
            name = checked.library.name
            if named is not None and name != named.name:
                raise LibraryError(f"the index lists `{named.name}` at {source}, and what is there calls itself "
                                   f"`{name}`. It was not added")
            if name in libraries:
                raise LibraryError(f"a library called `{name}` was added already, from {libraries[name].source}. "
                                   f"Bring it up to date with `media library update {name}`, or remove it first")
            folder = kept / name
            if folder.exists():
                shutil.rmtree(folder)
            (scratch / "clone").rename(folder)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        one = Added(name, source, folder, _commit(folder), False, now)
        checked = check(folder, measure=False)
    _write({**libraries, name: one}, settings)
    return one, checked


def update(name: str, *, ref: str | None = None, settings: Settings | None = None) -> tuple[Added, str | None]:
    """Bring an added library to `ref`, or to where its main branch now is. Returns it, and the commit it was at.

    One that has problems where it would be brought to is left where it was.
    """
    settings = settings or Settings.from_env()
    libraries = added(settings)
    one = _one(name, libraries)
    before = one.commit
    if one.linked:
        if ref is not None:
            raise LibraryError(f"`{name}` is a folder used where it is: check out the commit you want in it")
        load(one.folder)
    else:
        _git("fetch", "--quiet", "--tags", "origin", cwd=one.folder)
        _git("checkout", "--quiet", "--detach", ref or "origin/HEAD", cwd=one.folder)
        checked = check(one.folder, measure=False)
        if checked.library is None or checked.library.name != name:
            if before:
                _git("checkout", "--quiet", "--detach", before, cwd=one.folder)
            said = checked.problems or (f"it is now called `{checked.library.name}`",)
            raise LibraryError(f"`{name}` was left as it was, since where it would go has problems:\n  "
                               + "\n  ".join(said))
    one = Added(name, one.source, one.folder, _commit(one.folder), one.linked, one.added)
    _write({**libraries, name: one}, settings)
    return one, before


def remove(name: str, *, settings: Settings | None = None) -> Added:
    """Forget an added library. A clone made here goes with it; a folder used where it is, is left alone."""
    settings = settings or Settings.from_env()
    libraries = added(settings)
    one = _one(name, libraries)
    if not one.linked and one.folder.is_dir() and one.folder.parent == settings.home / "libraries":
        shutil.rmtree(one.folder)
    _write({other: kept for other, kept in libraries.items() if other != name}, settings)
    return one


def lock(file: str | Path = LOCK, *, settings: Settings | None = None) -> dict[str, Any]:
    """Write down which libraries this machine has and at what commit, for a project to keep."""
    file = Path(file)
    libraries = {name: {"source": one.source, "commit": one.commit}
                 for name, one in added(settings).items()}
    file.write_text(json.dumps({"libraries": libraries}, indent=2) + "\n", encoding="utf-8")
    return libraries


def install(file: str | Path = LOCK, *, settings: Settings | None = None,
            tell: Callable[[str], None] | None = None) -> list[Added]:
    """Add every library a project names, at the commit it names. One that is there at that commit is left."""
    file = Path(file)
    try:
        wanted = json.loads(file.read_text(encoding="utf-8"))["libraries"]
        if not isinstance(wanted, dict):
            raise TypeError
    except OSError:
        raise LibraryError(f"{file}: no such file. `media library lock` writes one") from None
    except (ValueError, KeyError, TypeError):
        raise LibraryError(f'{file} should hold {{"libraries": {{"name": {{"source": …, "commit": …}}}}}}') from None
    said = tell or (lambda message: None)
    done = []
    for name, one in sorted(wanted.items()):
        if not isinstance(one, dict) or not isinstance(one.get("source"), str):
            raise LibraryError(f"{file}: `{name}` does not say where it is from, as \"source\"")
        commit = one.get("commit")
        have = added(settings).get(name)
        source = one["source"]
        beside = (file.parent / source).expanduser()
        if not _SHORT.fullmatch(source) and "://" not in source and not source.startswith("git@") and beside.is_dir():
            source = str(beside.resolve())   # a folder is written from where the file is
        if have is None:
            linked = Path(source).is_dir()
            have, _ = add(source, ref=None if linked else commit, settings=settings)
            said(f"added {name} at {(have.commit or 'no commit')[:12]}")
        elif commit and have.commit != commit and not have.linked:
            have, _ = update(name, ref=commit, settings=settings)
            said(f"brought {name} to {commit[:12]}")
        else:
            said(f"{name} is here already")
        if have.name != name:
            raise LibraryError(f"{file} calls it `{name}`, and it calls itself `{have.name}`")
        if commit and have.commit != commit:
            said(f"  {name} is at {(have.commit or 'no commit')[:12]}, not the {commit[:12]} asked for: it is a "
                 "folder used where it is")
        done.append(have)
    return done


def every(settings: Settings | None = None) -> list[Library]:
    """Every added library that can be read as one. One that cannot is passed over."""
    found = []
    for one in added(settings).values():
        checked = check(one.folder, measure=False)
        if checked.library is not None:
            found.append(checked.library)
    return found


def banks(settings: Settings | None = None) -> list[Path]:
    """The folders of banks in the libraries added, for `@include` to look in after a script's own."""
    return [folder for library in every(settings) for folder in library.banks]


def sounds(settings: Settings | None = None) -> list[Path]:
    """The folders of sounds in the libraries added."""
    return [folder for library in every(settings) for folder in library.sounds]


def faces(settings: Settings | None = None) -> dict[str, Path]:
    """A picture for each persona that a library added has one for. The first library to have one wins."""
    found: dict[str, Path] = {}
    for library in every(settings):
        for persona, picture in library.faces.items():
            found.setdefault(persona, picture)
    return found


def voices(name: str, *, only: Iterable[str] = (), replace: bool = False, settings: Settings | None = None,
           tell: Callable[[str], None] | None = None) -> dict[str, list[str]]:
    """Give the personas of this machine the voices of an added library.

    A persona that is not there yet is added. One that has a voice already,
    from somewhere else, keeps it unless `replace`. Returns who was `given`
    a voice and who `kept` their own, each as `persona/emotion`, and who was
    `described`: a persona given a voice here, with nothing written about
    them yet, is given the words the library has. Words already written on
    this machine are never written over.
    """
    from . import personas, speech

    library = load(_one(name, added(settings)).folder)
    wanted = set(only)
    unknown = wanted - {voice.persona for voice in library.voices}
    if unknown:
        raise LibraryError(f"`{name}` has no voice for {', '.join(sorted(unknown))}. It has: "
                           + (", ".join(sorted({voice.persona for voice in library.voices})) or "none"))
    has = {(kept.voice.persona, kept.voice.emotion): kept.voice for kept in speech.kept()}
    said = tell or (lambda message: None)
    done: dict[str, list[str]] = {"given": [], "kept": [], "described": []}
    for voice in library.voices:
        if wanted and voice.persona not in wanted:
            continue
        who = f"{voice.persona}/{voice.emotion}"
        mine = has.get((voice.persona, voice.emotion))
        given = speech.Voice(voice.persona, voice.emotion, voice.engine, voice.profile or voice.persona,
                             str(voice.reference) if voice.reference else None, voice.said, voice.seed)
        # A voice that came from this library is this library's to give again.
        ours = mine is not None and (
            Path(mine.reference_audio).is_relative_to(library.folder) if mine.reference_audio
            else given.reference_audio is None and mine.profile == given.profile)
        if mine is not None and not ours and not replace:
            done["kept"].append(who)
            said(f"  {who} has a voice already, and keeps it (--replace gives it this one)")
            continue
        speech.keep(given, new=True)
        done["given"].append(who)
        said(f"  {who}" + (f", from {voice.reference.relative_to(library.folder)}" if voice.reference else
                           f", the server's `{voice.profile}`"))
    known = {one.slug: one for one in personas.listing()}
    for who in sorted({given.split("/")[0] for given in done["given"]}):
        about, here = library.personas.get(who), known.get(who)
        if about is None or not about.bio or here is None or here.bio:
            continue
        personas.keep(who, kind=about.kind, bio=about.bio)
        done["described"].append(who)
        said(f"  {who}: {about.bio}")
    return done


def _one(name: str, libraries: dict[str, Added]) -> Added:
    if name not in libraries:
        raise LibraryError(f"no library called `{name}` was added. Added: " + (", ".join(libraries) or "none"))
    return libraries[name]


def _commit(folder: Path) -> str | None:
    try:
        return _git("rev-parse", "HEAD", cwd=folder) or None
    except LibraryError:
        return None


def kept_clone(address: str, remote: str, kept: Path, *, refresh: bool = False, branch: str | None = None,
               fresh: float = 24 * 60 * 60) -> Path:
    """A clone of `remote` kept under `kept`, looked at again once it is `fresh` seconds old, or when asked.

    It is for reading: a shallow clone at the head of its main branch, or of `branch`. When it cannot be fetched
    again, what was fetched before is used. LibraryError when it cannot be cloned and nothing was kept of it.
    """
    folder = kept / (re.sub(r"[^a-z0-9]+", "-", address.lower()).strip("-")[:48] + "-"
                     + hashlib.sha256(address.encode()).hexdigest()[:8])
    stamp = folder / ".git" / "constructive-media-fetched"
    if folder.is_dir():
        try:
            is_fresh = time.time() - stamp.stat().st_mtime < fresh
        except OSError:
            is_fresh = False
        if is_fresh and not refresh:
            return folder
        try:
            _git("fetch", "--quiet", "origin", cwd=folder)
            _git("checkout", "--quiet", "--detach", f"origin/{branch}" if branch else "origin/HEAD", cwd=folder)
            stamp.touch()
        except LibraryError:
            pass                                    # offline: what was fetched before still stands
        return folder
    kept.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".fetching-", dir=kept))
    try:
        _git("clone", "--quiet", "--depth", "1", *(("--branch", branch) if branch else ()),
             "--no-recurse-submodules", "--", remote, str(scratch / "clone"))
        (scratch / "clone").rename(folder)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    stamp.touch()
    return folder


def _git(*arguments: str, cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(["git", *_FETCHING, *arguments], cwd=cwd, capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise LibraryError("git is needed to add a library from an address, and it is not installed") from None
    if done.returncode:
        told = (done.stderr.strip().splitlines() or ["it did not say why"])[-1]
        raise LibraryError(f"git could not {arguments[0]}: {told}")
    return done.stdout.strip()
