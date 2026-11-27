"""The script: the best passages as a document to mark up and hand back.

Most of an edit is decided by somebody reading. So the ranking can be written
out as plain text, a passage to a heading in the order it was said, with a
line under each to write a verdict on:

    ## 3fa9c21b77d0  harbour-interview  2:10.0-2:41.5  (32s)

    What was said in it.

    > keep - the best telling of this

The marks are `keep`, `cut`, `trim` and `?`, and anything after one is a note.
A line left empty says nothing. Reading the document back stores each mark as
a decision, and a cut also takes the passage out of every later ranking.

It goes round. A document written after marks were read has them already
filled in, and reading a document twice stores nothing the second time. A
mark that is not one of the four, or a heading that is not a passage, is
reported and left alone: nothing is guessed from a document a person wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import EditError, clock, decisions, scores

MARKS = ("keep", "cut", "trim", "?")
#: Enough of an id to tell ten thousand passages apart.
ID_LENGTH = 12

_HEADING = re.compile(r"^##\s+([0-9a-f]{6,32})\b")
_MARK = re.compile(r"^>\s*(\S.*?)?\s*$")
_VERDICT = re.compile(r"^(keep|cut|trim|\?)(?:\s*[-—–:]\s*|\s+|$)(.*)$", re.IGNORECASE)
_REMARK = re.compile(r"^\[(trim|\?)\]\s*(.*)$")


@dataclass
class Read:
    """What reading a marked-up script did."""

    kept: int = 0
    cut: int = 0
    noted: int = 0
    #: Marks that said what was already on record.
    unchanged: int = 0
    unmarked: int = 0
    #: Headings whose id is no passage's, or is more than one's.
    unknown: list[str] = field(default_factory=list)
    #: (id, what was written) for marks that are not one of the four.
    unreadable: list[tuple[str, str]] = field(default_factory=list)


def write(
    recipe: str = "interview", *, take: str | None = None, project: str | None = None,
    episode: str | None = None, limit: int = 40, min_coverage: float = 0.5, method: str | None = None,
) -> str:
    """The best `limit` passages by a recipe, in the order they were said, as a document."""
    ranking = scores.ranked(recipe, limit=limit, min_coverage=min_coverage, project=project, method=method,
                            take=take, episode=episode)
    if ranking.recipe.subject != "passage":
        raise EditError(f"recipe {recipe!r} ranks {ranking.recipe.subject}s; a script is made of passages")
    if not ranking.entries:
        raise EditError("there are no ranked passages to write out. `media passages` finds them and "
                        "`media score` scores them")
    entries = sorted(ranking.entries, key=lambda entry: (entry.source, entry.start_s))
    marked = decisions.standing(entry.subject_id for entry in entries)
    seconds = sum(entry.end_s - entry.start_s for entry in entries)
    lines = [
        f"# {take or project or 'Script'}: the best {len(entries)} passages by {ranking.recipe.name}",
        "",
        f"{clock(seconds)} in all. On the `>` line under a passage write keep, cut, trim or ?, and "
        "anything you want to add after it. Leave it empty to say nothing.",
    ]
    if ranking.withheld:
        lines += ["", f"{ranking.withheld} more would be here, but someone in them asks for it not to be used."]
    for entry in entries:
        heading = (f"## {str(entry.subject_id).replace('-', '')[:ID_LENGTH]}  {entry.source}  "
                   f"{clock(entry.start_s)}-{clock(entry.end_s)}  ({entry.end_s - entry.start_s:.0f}s)")
        lines += ["", heading, "", entry.text.strip() or "(no words)", "", _marked(marked.get(str(entry.subject_id)))]
    return "\n".join(lines) + "\n"


def _marked(ruling: tuple[bool | None, str | None] | None) -> str:
    """The `>` line that says what is already on record."""
    if ruling is None:
        return ">"
    kept, note = ruling
    if kept is False:
        # It is here, so the cut was from some episode this document is not
        # for. Written out, it would be read back as a cut from everything.
        return ">"
    mark = "keep"
    if kept is None:
        if not (found := _REMARK.match(note or "")):
            return ">"
        mark, note = found.groups()
    return f"> {mark}" + (f" - {note}" if note else "")


def read(text: str, *, episode: str | None = None, actor: str | None = None) -> Read:
    """Store the marks in a script. A cut is from `episode` when one is named, and from everything when not."""
    done = Read()
    current: str | None = None
    for line in text.splitlines():
        if heading := _HEADING.match(line):
            if current is not None:
                done.unmarked += 1
            current = heading.group(1)
            continue
        if current is None or not (written := _MARK.match(line)):
            continue
        passage, current = current, None
        said = (written.group(1) or "").strip()
        if not said:
            done.unmarked += 1
            continue
        if not (verdict := _VERDICT.match(said)):
            done.unreadable.append((passage, said))
            continue
        mark, note = verdict.group(1).lower(), verdict.group(2).strip() or None
        try:
            subject = decisions.find(passage, "passage")
        except EditError:
            done.unknown.append(passage)
            continue
        if mark == "keep":
            changed = decisions.keep(subject, note=note, episode=episode, actor=actor)
            done.kept += changed
        elif mark == "cut":
            changed = decisions.cut(subject, note=note, episode=episode, actor=actor)
            done.cut += changed
        else:
            changed = decisions.remark(subject, mark, note, actor=actor)
            done.noted += changed
        done.unchanged += not changed
    if current is not None:
        done.unmarked += 1
    return done
