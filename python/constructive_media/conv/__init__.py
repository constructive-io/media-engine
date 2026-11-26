"""Reading `.conv` scripts: a scene written as plain text.

    @characters
    ADA: { persona: ada }

    @sounds
    RAIN: { prompt: "steady rain on a tin roof", duration: 20, loop: true }
    ---
    [ambience RAIN fade-in:2]
    ADA: It has not stopped since Tuesday.
    [pause 0.8]
    ADA[quiet]: I have stopped minding.

The format is its own project, with its own specification and a reference
parser. This is the same grammar in Python, held to that project's files: every
script there must come out as the scene it gives, and every faulty one must
report the problems it gives, word for word.

    load      a script on disk as a scene, with the banks it includes found for it
    parse     a script's text as a scene, or `ConvError` with everything wrong in it
    banks     where `@include` finds a sound bank: `Files` on disk, `Memory` for tests
    plugins   what adds to the grammar: effects, slides, speakers, video, and your own
    schema    the keys each place in a script takes, and what makes a plugin

It differs from the reference in one place. Seconds that cannot be read, as in
`[pause 1.2.3]` or `<break 1.2.3>`, are a problem here, and no number there.

Reading a script runs no model and needs nothing installed.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from .banks import Bank, Files, Finder, Memory, Missing, read
from .parse import ConvError, Problem, Scene, in_order, parse, report
from .plugins import REVERBS, WIDGETS, defaults, effects, slides, speakers, video
from .schema import Key, Plugin
from .values import did_you_mean, similarity

__all__ = [
    "Bank", "ConvError", "Files", "Finder", "Key", "Memory", "Missing", "Plugin", "Problem", "REVERBS",
    "Scene", "WIDGETS", "defaults", "did_you_mean", "effects", "in_order", "load", "parse", "read",
    "report", "similarity", "slides", "speakers", "video",
]


def load(path: str | os.PathLike[str], *, banks: Sequence[str | os.PathLike[str]] = (), strict: bool = True,
         plugins: Sequence[Plugin] | None = None) -> Scene:
    """Read a script from a file.

    `banks` are the folders `@include NAME` looks in for `NAME.sounds`. With
    none, it looks in a `banks` folder beside the script, then in one in the
    working directory. An include written as a path is looked for beside the
    file that includes it, then from the working directory.
    """
    source = os.path.realpath(path)
    folders = list(banks) or [os.path.join(os.path.dirname(source), "banks"), "banks"]
    return parse(read(source), source=source, strict=strict, plugins=plugins,
                 banks=Files(folders, roots=[os.getcwd()]))
