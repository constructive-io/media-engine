"""A cut handed over whole: the files that open it, something to watch, and what was already made of it.

`export` writes what an editing program opens. Whoever the folder is sent to
usually wants to see the cut before opening anything, and whoever sends it
wants one folder to send and not three places to look in. So the folder an
export writes can be given two more things:

- **A preview.** The cut rendered small, to a size that goes in a message.
  The size is a limit and not a target: a short cut comes out as small as it
  would anyway. Where the file is recorded it is marked as a preview, so it
  is never taken for a render of the cut.
- **The renders.** Every file `media render` has made of the cut that is
  still where it was written. They are copied in and never moved: the folder
  is something to send, and what it was made from stays where it was. Each
  copy is recorded as a copy of its render, so `media lineage` can say what
  it is.

A render made before the cut was last saved is still copied, and said to be of
the cut as it was: whether it will do is for a person to decide. A render
whose file is gone is named and nothing is made up in its place.

The page written with the export lists what was put beside it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db, derive
from ..settings import Settings
from . import EditError, export, render, timelines
from .export import Exported
from .render import Rendered

#: How tall a preview is on each canvas: 540 on its short side.
PREVIEW_HEIGHTS = {"landscape": 540, "vertical": 960, "square": 540}
#: The most megabytes a preview is allowed, unless another number is given.
PREVIEW_MEGABYTES = 18.0


@dataclass(frozen=True)
class Made:
    """A render of a cut, as it was recorded."""

    asset_id: Any
    file: Path
    #: Whether the cut has been saved again since.
    stale: bool


@dataclass(frozen=True)
class Copied:
    """A render as it sits in the folder."""

    file: Path
    #: The render it is a copy of. The same file when the render was written here in the first place.
    of: Path
    stale: bool = False


@dataclass(frozen=True)
class Handed:
    """Everything one handing over put in a folder."""

    exported: Exported
    preview: Rendered | None = None
    copied: tuple[Copied, ...] = ()
    #: Renders on record whose files are no longer there.
    gone: tuple[Path, ...] = ()


def renders(name: str) -> list[Made]:
    """Every render of a cut on record, oldest first. A preview is not one."""
    cur = db.cursor()
    cur.execute(
        """SELECT o.id, o.path, d.created_at < t.run_at
             FROM media.derivations d
             JOIN media.assets o ON o.id = d.output_id
             JOIN media_video.timelines t ON t.name = d.params->>'timeline'
            WHERE d.kind = 'render' AND t.name = %s AND NOT jsonb_exists(d.params, 'preview')
            ORDER BY d.created_at, o.path""",
        (name,),
    )
    return [Made(asset_id, Path(path), bool(stale)) for asset_id, path, stale in cur.fetchall()]


def preview(
    name: str, folder: str | Path, *, megabytes: float = PREVIEW_MEGABYTES, fps: float | None = None,
    settings: Settings | None = None,
) -> Rendered:
    """Render a cut small into `folder` as `NAME-preview.mp4`, in no more than `megabytes`."""
    if megabytes <= 0:
        raise EditError("a preview has to be allowed some megabytes")
    cut = timelines.load(name)
    if not cut.clips:
        raise EditError(f"{name!r} has no clips to preview")
    height = PREVIEW_HEIGHTS.get(cut.canvas)
    width, tall = render.size(cut.canvas, height)
    per_s = render.rate(fps) if fps else render.usual(cut.clips, render.recordings(cut.clips))
    # What the file would weigh if it were given what a picture this size is worth.
    usual = (render.bitrate(width, tall, per_s) + render.SOUND_BITRATE) * cut.duration_s / 8_000_000
    return render.render(
        name, Path(folder).expanduser() / f"{name}-preview.mp4", height=height, fps=fps,
        megabytes=megabytes if usual > megabytes else None, settings=settings, note={"preview": True},
    )


def gather(name: str, folder: str | Path, taken: set[str] | None = None) -> tuple[list[Copied], list[Path]]:
    """Copy every render of a cut into `folder`. Returns the copies, and the renders that are gone.

    `taken` is the names already spoken for in the folder. A render that
    would take one of them, as two renders of one name from two folders
    would, is numbered. So is one that would be written over a file already
    there, unless that file is the copy made of the same render last time.
    """
    cut = timelines.load(name)
    target = Path(folder).expanduser().resolve()
    on_record = renders(name)
    taken = {each.lower() for each in taken or ()}
    taken |= {made.file.name.lower() for made in on_record if made.file.parent == target}
    copied: list[Copied] = []
    gone: list[Path] = []
    for made in on_record:
        if not made.file.is_file():
            gone.append(made.file)
            continue
        if made.file.parent == target:
            copied.append(Copied(made.file, made.file, made.stale))
            continue
        copy, number = target / made.file.name, 1
        while copy.name.lower() in taken or (copy.exists() and not _copy_of(copy, made.file)):
            number += 1
            copy = target / f"{made.file.stem}-{number}{made.file.suffix}"
        taken.add(copy.name.lower())
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(made.file, copy)
        with db.transaction():
            derive.record(copy, "export", "media export", [derive.Source(made.asset_id, "copied")],
                          params={"timeline": cut.name, "copy_of": str(made.file)}, project=cut.project)
        copied.append(Copied(copy, made.file, made.stale))
    return copied, gone


def _copy_of(file: Path, render_file: Path) -> bool:
    """Whether the file is on record as the copy made of this render."""
    known = derive.lineage(file)
    return bool(known and known.kind == "export" and known.params.get("copy_of") == str(render_file))


def hand_over(
    name: str, folder: str | Path, *, fps: float | None = None, title: str | None = None,
    with_preview: bool = False, megabytes: float = PREVIEW_MEGABYTES, with_renders: bool = False,
    settings: Settings | None = None,
) -> Handed:
    """Export a cut into `folder`, with a preview of it and the renders made of it when asked for."""
    target = Path(folder).expanduser().resolve()
    cut = timelines.load(name)
    seen = preview(name, target, megabytes=megabytes, fps=fps, settings=settings) if with_preview else None
    spoken_for = {f"{name}.fcpxml", f"{name}.edl", "README.md", f"{name}-preview.mp4"}
    copied, gone = gather(name, target, spoken_for) if with_renders else ([], [])
    beside: list[tuple[Path, str]] = []
    if seen is not None:
        beside.append((seen.path, f"the cut to watch, small: {seen.width}x{seen.height}"))
    for copy in copied:
        beside.append((copy.file, "a render of the cut" + (" as it was before it was last saved" if copy.stale else "")))
    exported = export.export(cut.name, target, fps=fps, title=title, beside=beside)
    return Handed(exported, seen, tuple(copied), tuple(gone))
