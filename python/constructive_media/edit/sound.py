"""What a cut is heard through: another recording of its take, or all its microphones mixed.

A cut is made from the recording that was easiest to read, which is seldom
the one that sounds best. Two things put that right without touching the
picture or the choice of clips:

- `relink` makes a copy of a cut heard through one recording of its take:
  the board feed in place of the camera's own microphone, say. Each clip's
  sound is moved to the same moment in that recording, by the offsets
  `media sync` found. A clip the recording does not cover is left as it was,
  and said to be.
- `followed` is what `media render --mix` does: under each stretch of sound,
  the take's worn microphones mixed so that whoever is speaking is up and
  the rest are down. Nothing is stored about it but the file that comes out.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .. import db
from ..takes import Member, Take, hand, members
from . import EditError, timelines
from .render import SAMPLES, Plan, Stretch
from .timelines import Clip, Timeline

#: How far apart two moments may be and still be the same one, in seconds.
SAME_S = 0.001
#: How far past its end a recording may be asked for, in seconds: lengths are stored rounded.
OVER_S = 0.05


@dataclass(frozen=True)
class Relinked:
    """A cut heard through another recording."""

    timeline: Timeline
    #: How many clips are now heard through it.
    changed: int
    #: The clips left as they were, each with its number from 1 and why.
    kept: tuple[tuple[int, str], ...]
    #: The recordings the sound now comes from, by label.
    through: tuple[str, ...]


def _shown(member: Member) -> str:
    """A recording as it is shown: its label, or its file's name."""
    return Path(member.label).name


def heard(clip: Clip) -> tuple[Any, float]:
    """The recording a clip is heard through, and where in it the clip begins."""
    if clip.sound is not None and clip.sound_start_s is not None:
        return clip.sound, clip.sound_start_s
    return clip.picture, clip.start_s


def takes_of(asset_id: Any) -> list[Take]:
    """The takes a recording is in."""
    cur = db.cursor()
    cur.execute(
        """SELECT t.id, t.name, t.project FROM media.take_assets ta JOIN media.takes t ON t.id = ta.take_id
            WHERE ta.asset_id = %s ORDER BY t.name""",
        (asset_id,),
    )
    return [Take(*row) for row in cur.fetchall()]


class _Takes:
    """Takes and their recordings, each looked up once."""

    def __init__(self, only: Take | None = None) -> None:
        self.only = only
        self._of: dict[str, list[Take]] = {}
        self._members: dict[str, dict[str, Member]] = {}

    def of(self, asset_id: Any) -> list[Take]:
        if self.only is not None:
            return [self.only]
        if str(asset_id) not in self._of:
            self._of[str(asset_id)] = takes_of(asset_id)
        return self._of[str(asset_id)]

    def members(self, take: Take) -> dict[str, Member]:
        if str(take.id) not in self._members:
            self._members[str(take.id)] = {str(member.asset_id): member for member in members(take.id)}
        return self._members[str(take.id)]


def _moved(clip: Clip, takes: _Takes, recording: str, wanted: Member | None) -> tuple[Clip | None, str, str | None]:
    """One clip heard through the recording, or nothing and why not. Last is the label of what it is heard through."""
    asset, begins = heard(clip)
    why = "is from a recording that is in no take"
    for take in takes.of(asset):
        inside = takes.members(take)
        anchor = inside.get(str(asset))
        if anchor is None or not anchor.placed:
            why = "is from a recording that has no place in the take" if anchor else why
            continue
        if wanted is not None:
            found = [wanted]
        else:
            found = hand.fits(take, recording)
        if len(found) != 1:
            why = (f"is in take {take.name!r}, where nothing is called {recording!r}" if not found
                   else f"is in take {take.name!r}, where {recording!r} could be any of {len(found)} recordings")
            continue
        to = found[0]
        called = _shown(to)
        if not to.has_audio:
            raise EditError(f"{called} has no sound to hear a cut through")
        if not to.placed:
            why = f"cannot be found in {called}, which has no place in the take yet; `media sync` gives it one"
            continue
        if str(to.asset_id) == str(asset):
            return None, "is heard through it already", called
        start = begins + float(anchor.offset_s) - float(to.offset_s)
        length = clip.duration_s
        if start < -SAME_S or (to.duration_s is not None and start + length > float(to.duration_s) + OVER_S):
            why = f"was said while {called} was not recording"
            continue
        start = max(start, 0.0)
        own = (str(to.asset_id) == str(clip.picture) and abs(start - clip.start_s) < SAME_S
               and abs((clip.end_s - clip.start_s) - length) < SAME_S)
        if own:         # back to the sound its own picture came with
            return dataclasses.replace(clip, sound=None, sound_start_s=None, sound_end_s=None), "", called
        return dataclasses.replace(clip, sound=to.asset_id, sound_start_s=start, sound_end_s=start + length), "", called
    return None, why, None


def relink(source: str, name: str, recording: str, *, take: str | None = None) -> Relinked:
    """Store `source` as `name`, heard through `recording`.

    `recording` is one recording of the take each clip was recorded in, by
    its label, its file's name or a pattern that fits one. Give `take` when
    the cut draws on one take and the name should be checked before anything
    is done. The picture, the clips and their order stay as they are.
    """
    cut = timelines.load(source)
    if not cut.clips:
        raise EditError(f"{source!r} has no clips")
    only = wanted = None
    if take is not None:
        from .. import takes as known

        only = known.find(take)
        wanted = hand.member(only, recording)
    takes = _Takes(only)
    clips, kept, through, changed = [], [], [], 0
    for number, clip in enumerate(cut.clips, 1):
        moved, why, label = _moved(clip, takes, recording, wanted)
        if label and label not in through:
            through.append(label)
        if moved is None:
            kept.append((number, why))
            clips.append(clip)
        else:
            changed += 1
            clips.append(moved)
    if not changed:
        reasons = sorted({why for _, why in kept})
        raise EditError(f"no clip of {source!r} could be heard through {recording!r}: "
                        + ("every one " + reasons[0] if len(reasons) == 1 else "; ".join(reasons)))
    stored = timelines.save(
        name, clips, kind=cut.kind, project=cut.project, title=cut.title,
        after=None if name == source else source, episode=cut.episode, canvas=cut.canvas, fit=cut.fit,
        style=cut.style, seed=cut.seed,
        params={**cut.params, "sound": {"from": source, "recording": recording, "take": take}},
    )
    return Relinked(stored, changed, tuple(kept), tuple(through))


def followed(clips: Sequence[Clip], planned: Plan, folder: Path, *, duck_db: float | None = None) -> tuple[Plan, list[Any]]:
    """A plan with each stretch of sound replaced by its take's microphones mixed, where there are any.

    Returns the plan and what was mixed, in order. A stretch is left as it
    was when its recording is in no take with two worn microphones, or they
    were not recording through it: one recording whole is better than a mix
    with a hole in it. The mixed files are written into `folder`, which is
    the caller's to remove once the plan has been built.
    """
    from ..takes.mix import DUCK_DB, Mixer

    takes = _Takes()
    mixers: dict[str, Mixer] = {}
    stretches, mixes = [], []
    for number, stretch in enumerate(planned.stretches):
        made = None
        if stretch.file is not None and stretch.samples:
            asset, _ = heard(clips[stretch.first])
            for take in takes.of(asset):
                anchor = takes.members(take).get(str(asset))
                if anchor is None or not anchor.placed:
                    continue
                if str(take.id) not in mixers:
                    mixers[str(take.id)] = Mixer(take, duck_db=DUCK_DB if duck_db is None else duck_db)
                mixer = mixers[str(take.id)]
                if not mixer.able:
                    continue
                begins = stretch.start_s + float(anchor.offset_s)
                made = mixer.mix(begins, begins + stretch.samples / SAMPLES, folder / f"mix-{number:04d}.wav")
                if made is not None:
                    break
        if made is None:
            stretches.append(stretch)
            continue
        mixes.append(made)
        stretches.append(Stretch(made.file, 0.0, stretch.samples, stretch.first, stretch.count, stretch.gain_db,
                                 stretch.opens, stretch.closes))
    return (Plan(planned.width, planned.height, planned.rate, planned.fit, planned.pieces, tuple(stretches),
                 planned.times), mixes)


__all__ = ["Relinked", "followed", "heard", "relink", "takes_of"]
