"""Reading a `.conv` script into a scene.

A scene is plain data, the shape the format's own JSON takes:

    characters  each character's settings, by name
    sounds      each sound's settings, by name, the included ones too
    tracks      each declared track's settings, by name
    items       the script in order: line, pause, cue, track and mark
    problems    what is wrong, when the script was read without `strict`
    index       the line each character, sound, track, speaker and file is first named on

Nothing wrong in a script is dropped quietly. Every problem carries its line,
what is wrong and how to mend it, and a strict reading raises them all at once.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .banks import Finder, Missing
from .plugins import defaults
from .schema import Plugin, Registry, registry, shown
from .segments import pieces
from .values import Asking, Pair, check, did_you_mean, number, read_declaration, read_overrides, read_play, trim

Problem = dict[str, Any]     # line, text, message
Scene = dict[str, Any]


def _either(word: str) -> str:
    """A word in a pattern, in capitals or small letters."""
    return "".join(f"[{letter.lower()}{letter.upper()}]" for letter in word)


_END = r"(?![A-Za-z0-9_])"
_INCLUDE = re.compile(rf"@include{_END}\s*(.*)\Z")
_PAUSE = re.compile(rf"\[{_either('pause')}\s+([0-9.]+)\]\Z")
_PAUSE_OPENS = re.compile(rf"\[{_either('pause')}{_END}")
_OLD_MUSIC = re.compile(rf"\[{_either('music')}:\s*([^\s\]]+)\s*([^\]]*)\]\Z")
_DIRECTIVE = re.compile(r"\[([a-z][a-z0-9_-]*)(?:\s+([^\]]*))?\]\Z")
_CUE = re.compile(r"\[([A-Z][A-Z0-9_-]*)(?:\s+([^\]]*))?\]\Z")
_SOUND_NAME = re.compile(r"[A-Z][A-Z0-9_-]*\Z")
_SOUND = re.compile(r"([A-Z][A-Z0-9_-]*)\s*:\s*\{(.+)\}\s*\Z")
_CHARACTER = re.compile(r"([A-Z][A-Z0-9_]*)\s*:\s*\{(.+)\}\s*\Z")
_TRACK = re.compile(r"([a-z][a-z0-9_-]*)\s*:\s*\{(.*)\}\s*\Z")
_TRACK_OPENS = re.compile(r"([a-z][a-z0-9_-]*)\s*:\s*\{")
_LINE = re.compile(r"([A-Z][A-Z0-9_]*)(?:\[([^\]]*)\])?\s*:\s*(.+)\Z")
_OLD_CHARACTER = re.compile(r"([A-Z][A-Z0-9_]*)\s*:\s*([a-z][a-z0-9_-]*)\s*\Z")

_OLD_PAUSE = re.compile(rf"{_either('pause')}\s+([0-9.]+)\Z")
_ANY_DECLARED = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)\s*:\s*\{")
_ANY_LINE = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)(\[[^\]]*\])?\s*:\s*(.+)\Z")
_ANY_BRACKET = re.compile(r"\[([A-Za-z][A-Za-z0-9_-]*)")

_HEADERS = ("@characters", "@sounds", "@tracks", "@include")
_PARTS = {"@characters": "characters", "@voices": "characters", "@sounds": "sounds",
          "@tracks": "tracks", "---": "body"}
_STOPS = ("off", "none", "stop")


def in_order(problems: Sequence[Problem]) -> list[Problem]:
    """Problems as the script runs, those with no line last."""
    return sorted(problems, key=lambda problem: (problem["line"] is None, problem["line"] or 0))


def report(problems: Sequence[Problem], source: str | None = None) -> str:
    """Every problem in script order: its line, what is wrong and how to mend it."""
    count = len(problems)
    out = [f"{source or '.conv script'}: {count} problem{'' if count == 1 else 's'}"]
    for problem in in_order(problems):
        out.append("")
        out.append(f"  line {problem['line']}: {problem['text']}" if problem["line"] else "  (script)")
        first, *rest = problem["message"].split("\n")
        out.append("    " + first)
        out.extend("      " + part for part in rest)
    return "\n".join(out)


class ConvError(Exception):
    """A script with something wrong in it. It carries every problem, not only the first."""

    def __init__(self, problems: Sequence[Problem], source: str | None = None):
        super().__init__(report(problems, source))
        self.problems = in_order(problems)
        self.source = source


def parse(text: str, *, source: str | None = None, strict: bool = True,
          plugins: Sequence[Plugin] | None = None, banks: Finder | None = None) -> Scene:
    """Read a script.

    `source` names it in what is reported, and is where its includes are looked
    for from. With `strict` a script with problems raises `ConvError`; without,
    they come back in the scene's `problems`. `plugins` replace the usual ones:
    pass `[*defaults(), yours]` to add to them. `banks` finds what `@include`
    names; with none, an include is a problem.
    """
    made = registry(defaults() if plugins is None else plugins)
    return _read(text, source, strict, made, banks, False, [source] if source else [])


def _read(text: str, source: str | None, strict: bool, made: Registry, banks: Finder | None,
          bank: bool, within: list[str]) -> Scene:
    scene = _Script(text, made, banks, bank, within).read()
    if scene["problems"] and strict:
        raise ConvError(scene["problems"], source)
    scene["problems"] = in_order(scene["problems"])
    return scene


def _hint(said: str, part: str | None) -> str:
    """What a line that is nothing known was probably meant to be."""
    match = _OLD_PAUSE.match(said)
    if match:
        return f"pauses go in brackets: `[pause {match[1]}]` (this is the old v1 form)"
    match = _ANY_DECLARED.match(said)
    if match and match[1] != match[1].upper():
        if part == "tracks":
            return f"track names are lowercase: `{match[1].lower()}: {{ … }}`"
        return f"names are UPPERCASE: `{match[1].upper()}: {{ … }}`"
    match = _ANY_LINE.match(said)
    if match:
        name = match[1]
        if "-" in name:
            return f"speaker names use letters, digits and _ — not -: `{name.replace('-', '_').upper()}: …`"
        if name != name.upper():
            return f"speaker names are UPPERCASE: `{name.upper()}{match[2] or ''}: {match[3][:30]}…`"
    match = _ANY_BRACKET.match(said)
    if match:
        return (f"`[{match[1]} …]` — sound names are UPPERCASE (`[{match[1].upper()}]`), "
                f"track directives lowercase (`[{match[1].lower()} SOUND]`)")
    if part == "tracks":
        return "inside @tracks every line is `name: { key: value, … }`"
    return ("expected one of: `NAME: text`, `NAME[emotion]: text`, `[pause N]`, `[SOUND]`, "
            "`[track SOUND]`, `# comment`, or a header (@characters, @sounds, @tracks, ---)")


@dataclass
class _Included:
    """What a script's includes bring in."""
    sounds: dict[str, dict[str, Any]] = field(default_factory=dict)
    tracks: dict[str, dict[str, Any]] = field(default_factory=dict)
    # (sound or track, name) -> the bank it came from, and the line that included it
    origin: dict[tuple[str, str], tuple[str, int]] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    # A name two banks both define: (sound or track, name, the problem to report).
    clashes: list[tuple[str, str, Problem]] = field(default_factory=list)


class _Script:
    """One script, or one bank, being read."""

    def __init__(self, text: str, made: Registry, banks: Finder | None, bank: bool, within: list[str]):
        self.lines = [trim(line) for line in re.split(r"\r?\n", text)]
        self.made = made
        self.banks = banks
        self.bank = bank                 # a bank holds sounds and tracks, and nothing else
        self.within = within             # what includes this, outermost first
        self.characters: dict[str, dict[str, Any]] = {}
        self.sounds: dict[str, dict[str, Any]] = {}
        self.tracks: dict[str, dict[str, Any] | None] = {}
        self.items: list[dict[str, Any]] = []
        self.problems: list[Problem] = []
        # Every sound a line names, to check once every declaration has been read.
        self.named: list[tuple[int, str, str]] = []
        self.index: dict[str, dict[str, dict[str, Any]]] = {
            "character": {}, "sound": {}, "track": {}, "speaker": {}, "file": {},
        }
        self.part: str | None = None     # characters, sounds, tracks, body or rejected
        self.started = False             # a pause, a mark or a line has been read

    # --- reading ---------------------------------------------------------------------

    def read(self) -> Scene:
        included = self._include()
        self.sounds.update(included.sounds)
        self.tracks.update(included.tracks)
        self.problems.extend(included.problems)
        for (kind, name), (ref, line) in included.origin.items():
            self.index[kind][name] = {"line": line, "include": ref}
        self._track_names()

        for number_, said in enumerate(self.lines, start=1):
            wrong: list[str] = []
            self._line(number_, said, wrong)
            self.problems.extend({"line": number_, "text": said, "message": message} for message in wrong)

        # Two banks defining one name is a problem unless the script settles it.
        for kind, name, problem in included.clashes:
            if "include" in self.index[kind].get(name, {}):
                self.problems.append(problem)
        for line, said, sound in self.named:
            if sound not in self.sounds:
                self.problems.append({"line": line, "text": said, "message": (
                    f"sound `{sound}` isn't declared under @sounds" + did_you_mean(sound, self.sounds)
                    + f"\nAdd it, e.g. `{sound}: {{ file: sounds/{sound.lower()}.wav }}` "
                    f"or `{sound}: {{ prompt: \"…\", duration: 3 }}`")})
        # A bare [CUE] plays on the track its sound names, or else in the foreground.
        for item in self.items:
            if item["type"] == "cue":
                sound = self.sounds.get(item["sound"], {})
                track = sound.get("track", sound.get("layer"))
                if track and self._known(track):
                    item["track"] = track

        scene: Scene = {
            "characters": self.characters, "sounds": self.sounds,
            "tracks": {name: settings or {} for name, settings in self.tracks.items()},
            "items": self.items, "problems": self.problems, "index": self.index,
        }
        for finalize in self.made.finalizers:
            finalize(scene, self._against)
        return scene

    def _against(self, item: dict[str, Any], message: str) -> None:
        line = item["line"]
        said = self.lines[line - 1] if 0 < line <= len(self.lines) else None
        self.problems.append({"line": line, "text": said, "message": message})

    def _include(self) -> _Included:
        included = _Included()
        banks = self.banks.names() if self.banks else []
        listed = f"\nBanks: {', '.join(banks)}" if banks else ""
        for number_, said in enumerate(self.lines, start=1):
            if said == "---":
                break
            match = _INCLUDE.match(said)
            if not match:
                continue

            def problem(message: str) -> Problem:
                return {"line": number_, "text": said, "message": message}

            ref = trim(match[1])
            if not ref:
                included.problems.append(problem("`@include` needs a bank, e.g. `@include podcast`" + listed))
                continue
            if self.banks is None:
                included.problems.append(problem(
                    f"can't include `{ref}` — this parser was given no include resolver"))
                continue
            found = self.banks.find(ref, self.within[-1] if self.within else None)
            if isinstance(found, Missing):
                included.problems.append(problem(
                    f"can't find `{ref}` — looked for {', '.join(found.tried)}" + did_you_mean(ref, banks) + listed))
                continue
            if found.id in self.within:
                included.problems.append(problem(
                    f"`@include {ref}` goes round in a circle: {' -> '.join([*self.within, found.id])}"))
                continue
            try:
                bank = _read(found.text, found.id, True, self.made, self.banks, True, [*self.within, found.id])
            except ConvError as error:
                detail = "\n".join(f"line {inner['line']}: " + inner["message"].replace("\n", "\n  ")
                                   for inner in error.problems)
                included.problems.append(problem(f"`{ref}` ({found.id}) has problems — fix them there:\n{detail}"))
                continue
            for kind, got, into in (("sound", bank["sounds"], included.sounds),
                                    ("track", bank["tracks"], included.tracks)):
                for name, settings in got.items():
                    before = included.origin.get((kind, name))
                    if before:
                        included.clashes.append((kind, name, problem(
                            f"{kind} `{name}` comes from both `{before[0]}` and `{ref}` — which one "
                            f"is meant is ambiguous.\nDeclare `{name}` in this script to choose, or include "
                            "only one of them.")))
                        continue
                    into[name] = dict(settings)
                    included.origin[(kind, name)] = (ref, number_)
        return included

    def _track_names(self) -> None:
        """Find every declared track first, so that anything above @tracks can already name one."""
        inside = False
        for said in self.lines:
            if said == "@tracks":
                inside = True
            elif said.startswith("@") or said == "---":
                inside = False
            elif inside:
                match = _TRACK_OPENS.match(said)
                if match and match[1] not in self.made.reserved and match[1] not in self.tracks:
                    self.tracks[match[1]] = None

    # --- what is in scope ------------------------------------------------------------

    def _every_track(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.made.tracks, *self.tracks]))

    def _known(self, track: str) -> bool:
        return track in self.made.tracks or track in self.tracks

    def _asking(self, what: str, context: str | None = None) -> Asking:
        return Asking(self.made, self._every_track(), what, context)

    # --- one line --------------------------------------------------------------------

    def _line(self, line: int, said: str, wrong: list[str]) -> None:
        made = self.made
        if not said or said.startswith("#"):
            return
        if _INCLUDE.match(said):
            if self.part == "body" or self.started:
                wrong.append("`@include` belongs in the header, above `---`")
            return
        if self.bank and said in ("@characters", "@voices", "---"):
            wrong.append("a sound bank holds only @sounds and @tracks (and @include) — characters and the "
                         "script belong in the .conv")
            self.part = "rejected"
            return
        if said in _PARTS:
            self.part = _PARTS[said]
            return
        if said.startswith("@"):
            wrong.append(f"unknown header `{said}` — headers are @characters, @sounds, @tracks and @include"
                         + did_you_mean(said, _HEADERS))
            return
        if self.bank and self.part not in ("sounds", "tracks"):
            if self.part != "rejected":
                wrong.append("a sound bank holds only @sounds and @tracks — put this line under one of those headers")
            return

        match = _PAUSE.match(said)
        seconds = number(match[1]) if match else None
        if seconds is not None:
            self.items.append({"type": "pause", "line": line, "seconds": seconds})
            self.started = True
            return
        if _PAUSE_OPENS.match(said):
            wrong.append("`[pause N]` needs seconds, e.g. `[pause 1.5]`")
            return

        if self.part == "tracks":
            match = _TRACK.match(said)
            if match and match[1] in made.reserved:
                wrong.append(f"`{match[1]}` is a reserved word and can't name a track — pick another name")
                return
            if match:
                pairs = read_declaration(match[2], wrong)
                self.tracks[match[1]] = check(pairs, made.schemas["track"], self._asking(f"track `{match[1]}`"), wrong)
                self.index["track"][match[1]] = {"line": line}
                return

        match = _OLD_MUSIC.match(said)
        if match:
            settings = check(read_play(match[2]), made.schemas["play"], self._asking("`[music: …]`"), wrong)
            self.items.append({"type": "track", "line": line, "track": "music", "action": "play",
                               "sound": None, "file": match[1], "overrides": settings})
            self.index["file"].setdefault(match[1], {"line": line})
            return

        match = _DIRECTIVE.match(said)
        if match and match[1] in made.directives:
            data = made.directives[match[1]](trim(match[2] or ""), wrong.append)
            if not wrong:
                self.items.append({"type": "mark", "line": line, "kind": match[1], "data": data or {}})
            self.started = True
            return
        if match and match[1] not in made.reserved:
            name, rest = match[1], match[2] or ""
            if not self._known(name):
                if name.upper() in self.sounds:
                    meant = f" — sound names are UPPERCASE: `[{name.upper()}{' ' + rest if rest else ''}]`"
                else:
                    meant = did_you_mean(name, self._every_track())
                wrong.append(f"unknown track `{name}`{meant}\nBuilt-in tracks: {', '.join(made.tracks)}. "
                             f"Declare others under @tracks, e.g. `{name}: {{ level: -24 }}`")
                return
            self.items.append(self._on_track(name, rest, line, said, wrong))
            return

        if self.part == "sounds":
            match = _SOUND.match(said)
            if match:
                name = match[1]
                settings = check(read_declaration(match[2], wrong), made.schemas["sound"],
                                 self._asking(f"sound `{name}`"), wrong)
                if not settings.get("prompt") and not settings.get("file"):
                    wrong.append(f"sound `{name}` needs a source: `file: path/to.mp3` or "
                                 "`prompt: \"what it sounds like\"`")
                elif settings.get("prompt") and settings.get("file"):
                    wrong.append(f"sound `{name}` has both `file:` and `prompt:` — keep one")
                self.sounds[name] = settings
                self.index["sound"][name] = {"line": line}
                return

        if self.part != "body":
            match = _CHARACTER.match(said)
            if match:
                name = match[1]
                settings = check(read_declaration(match[2], wrong), made.schemas["character"],
                                 self._asking(f"character `{name}`"), wrong)
                # `voice:` and `speaker:` are other words for the persona.
                other = settings.get("voice", settings.get("speaker"))
                if other and not settings.get("persona"):
                    settings["persona"] = str(other).lower()
                self.characters[name] = settings
                self.index["character"][name] = {"line": line}
                self.part = "characters"
                return

        if self.part == "characters" and not self.started:
            match = _OLD_CHARACTER.match(said)
            if match:
                wrong.append(f"character definitions need braces: `{match[1]}: {{ persona: {match[2]} }}` "
                             f"(`{match[1]}: {match[2]}` is the old v1 form)")
                return

        match = _CUE.match(said)
        if match:
            settings = check(read_play(match[2]), made.schemas["play"], self._asking(f"[{match[1]}]"), wrong)
            self.named.append((line, said, match[1]))
            self.items.append({"type": "cue", "line": line, "sound": match[1], "track": "foreground",
                               "overrides": settings})
            return

        match = _LINE.match(said)
        if match:
            self.part = "body"
            self.started = True
            speaker, words = match[1], trim(match[3])
            settings = check(read_overrides(match[2] or "", wrong), made.schemas["line"],
                             self._asking(f"`{speaker}[…]`", "line"), wrong)
            self.items.append({"type": "line", "line": line, "speaker": speaker, "text": words,
                               "overrides": settings, "segments": self._segments(words, line, said, wrong)})
            self.index["speaker"].setdefault(speaker, {"line": line})
            return

        wrong.append(_hint(said, self.part))

    def _segments(self, words: str, line: int, said: str, wrong: list[str]) -> list[dict[str, Any]]:
        made = self.made
        segments: list[dict[str, Any]] = []
        for piece in pieces(words):
            if piece.type == "break":
                if piece.seconds is None:
                    wrong.append(f"`{piece.raw}` needs seconds, e.g. `<break 0.5>`")
                else:
                    segments.append({"type": "break", "seconds": piece.seconds})
            elif piece.type == "sfx":
                self.named.append((line, said, piece.sound))
                segments.append({"type": "sfx", "sound": piece.sound, "overrides": check(
                    read_play(piece.raw), made.schemas["play"], self._asking(f"<sfx {piece.sound}>"), wrong)})
            else:
                settings = check(read_overrides(piece.raw, wrong), made.schemas["span"],
                                 self._asking("an inline `{…: …}` span", "span"), wrong) if piece.raw else {}
                segments.append({"type": "text", "text": piece.text, "overrides": settings})
        return segments

    def _on_track(self, name: str, rest: str, line: int, said: str, wrong: list[str]) -> dict[str, Any]:
        """`[music BED fade-in:3]`, `[music off]`, `[music vol:0.5 ramp:2]`."""
        made = self.made
        words = [word for word in re.split(r"[,\s]+", trim(rest)) if word]
        sound, action = None, "set"
        if words and words[0].lower() in _STOPS:
            action, words = "off", words[1:]
            if words and _SOUND_NAME.match(words[0]):
                sound, words = words[0], words[1:]
        elif words and _SOUND_NAME.match(words[0]):
            action, sound, words = "play", words[0], words[1:]
        pairs: list[Pair] = read_play(",".join(words))
        if action == "play":
            schema = made.schemas["play"]
        elif action == "off":
            schema = made.schemas["off"]
        elif not self.started:
            # Before the first line, a directive can settle anything a track declaration can.
            schema = {**made.schemas["track"], **made.schemas["set"]}
        else:
            schema = made.schemas["set"]
        item = {"type": "track", "line": line, "track": name, "action": action, "sound": sound, "overrides": {}}

        if action == "set" and not pairs:
            wrong.append(f"`[{name}]` does nothing — play a sound (`[{name} SOUND]`), stop (`[{name} off]`), "
                         f"or set something (`[{name} vol:0.5]`)")
        elif action == "set" and self.started:
            other = [pair for pair in pairs if pair.key not in schema]
            if other and all(pair.key in made.schemas["track"] for pair in other):
                first = other[0]
                value = "true" if first.value is True else first.value
                wrong.append(f"`{', '.join(shown(pair.key) for pair in other)}` can't change mid-scene — only `vol` "
                             "(with `ramp`) moves over time.\nPut it on the track instead: under @tracks, "
                             f"`{name}: {{ {shown(first.key)}: {value} }}`")
                return item
        what = {"play": f"a [{name} …] play", "off": f"[{name} off]"}.get(action, f"[{name} …]")
        item["overrides"] = check(pairs, schema, self._asking(what), wrong)
        for rule in made.track_rules.get(name, ()):
            rule(item, wrong.append)
        if sound:
            self.named.append((line, said, sound))
        return item
