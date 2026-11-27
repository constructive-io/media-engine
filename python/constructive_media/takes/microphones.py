"""Who spoke each line, told by whose microphone heard it loudest.

When everyone at a table wears a microphone, every recording hears everyone,
but each hears its own wearer best. So a line is given to whoever's microphone
was loudest while it was said. It takes two things a person tells the engine,
that these recordings are one take (`sync` places them) and who wore which
(`wear`), and nothing else: no model, and no voice has to be known first.

Each microphone is measured against itself, not against the others. Recorders
are set to different levels, so the one turned up highest would win every
line. What a microphone is measured against is its own loud level, the level
it is under nineteen twentieths of the time, and not its quietest: a recorder
that shuts off between words has a silence a hundred times deeper than one
that does not, and measured from there everything it heard would look loud.

A line is given only when one microphone leads the next clearly, and was not
itself far under its own loud level. Otherwise it is left without a name: two
people talking at once, someone with no microphone, a line too short to
measure. A wrong name said with confidence is worse than a gap. How far the
loudest led is kept beside each line, since that is how sure the answer is.

What this answers is which microphone best heard the moment. That is who
spoke only where everyone speaking wears one. And it has a weakness: someone
who says very little has a loud level set by other people's voices, and their
microphone can take lines that are not theirs. Comparing the recordings with
each other again cannot show that, because it asks the same question of the
same sound. The voices `diarize` found can: they were told apart by how they
sound, which this never looked at. So for each microphone the lines it won are
counted by voice, and a microphone that carries more than one is said to.

Nothing a person said is replaced. A line somebody gave a speaker is left as
it is. So is a line whose voice was named, with `name`, as someone other than
the microphone's wearer: the two disagree, and that is reported, not settled.
Which lines were given a speaker here is written on each recording, so going
over a take again takes back exactly those and no others.

Needs numpy.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from .. import db
from ..settings import Settings
from ..video.loudness import RATE, levels
from . import Take, TakeError, find, members

#: How far the loudest microphone must lead the next to be believed, in decibels.
MARGIN_DB = 4.0
#: How far under its own loud level a microphone may be and still be given a line.
FLOOR_DB = -18.0
#: The least of a line a microphone must have been recording, in seconds.
SHORTEST_S = 0.2
#: A microphone whose loud level is under this heard nothing: it was off, or not plugged in.
SILENT = 1e-4
#: The share of what a microphone won that one voice should carry.
ONE_VOICE = 0.8
#: Where, on a recording, the lines given a speaker here are written down.
_NOTED = "microphones"

#: A listening clip is made of lines the microphone won by this much,
CLIP_LEAD_DB = 9.0
#: a moment of this many seconds from the middle of each,
CLIP_MOMENT_S = 1.5
#: and at most this many of them, spread over the whole take.
CLIP_MOMENTS = 12

#: What became of a line.
GIVEN, UNCLEAR, UNHEARD, UNWORN, BY_HAND, DISAGREES = "given", "unclear", "unheard", "unworn", "by hand", "disagrees"


@dataclass(frozen=True)
class Microphone:
    """One recording of a take that somebody wore, or could have."""

    asset_id: Any
    name: str
    #: The file to read.
    source: str
    #: Seconds to add to its own time to reach take time.
    offset_s: float
    #: Who wore it, when that has been said.
    wearer_id: Any = None
    wearer: str | None = None


@dataclass(frozen=True)
class Choice:
    """Which microphone heard one stretch loudest."""

    #: Its place among the microphones, when it led clearly enough to be believed.
    winner: int | None = None
    #: How far the loudest led the next, in decibels. None when fewer than two heard the stretch.
    lead_db: float | None = None
    #: How loud the loudest was, against its own loud level.
    level_db: float | None = None


@dataclass(frozen=True)
class Line:
    """One transcribed line of a take, and the microphone that heard it loudest."""

    segment_id: Any
    #: The recording whose transcript it is a line of.
    asset_id: Any
    #: When it was said, in take time.
    start_s: float
    end_s: float
    #: The microphone that won it, when one did.
    microphone: Microphone | None = None
    lead_db: float | None = None
    #: Whose a person has already said the line is.
    named_id: Any = None
    #: The voice `diarize` heard say it, and whose a person said that voice is.
    voice: str | None = None
    voice_owner_id: Any = None
    voice_owner: str | None = None

    @property
    def outcome(self) -> str:
        """What is done with the line: one of `GIVEN`, `UNCLEAR`, `UNHEARD`, `UNWORN`, `BY_HAND`, `DISAGREES`."""
        if self.microphone is None:
            return UNHEARD if self.lead_db is None else UNCLEAR
        if self.named_id is not None:
            return BY_HAND
        if self.microphone.wearer_id is None:
            return UNWORN
        if self.voice_owner_id is not None and self.voice_owner_id != self.microphone.wearer_id:
            return DISAGREES
        return GIVEN


@dataclass(frozen=True)
class Reading:
    """A take's lines, each with the microphone that heard it loudest."""

    take: Take
    microphones: list[Microphone]
    lines: list[Line]
    margin_db: float = MARGIN_DB
    #: Microphones that recorded nothing, by name. They take no part.
    silent: list[str] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(line.outcome == outcome for line in self.lines)


@dataclass(frozen=True)
class OneVoice:
    """The voice that says most of what a microphone won, in the recording that heard most of it."""

    #: Its label there, and the recording's name.
    label: str
    recording: str
    #: The share of the microphone's lines in that recording that are this voice's, by time.
    share: float
    #: Whose a person said the voice is.
    owner: str | None = None

    @property
    def alone(self) -> bool:
        return self.share >= ONE_VOICE


@dataclass(frozen=True)
class Won:
    """What one microphone won."""

    microphone: Microphone
    lines: int
    seconds: float
    #: The middle of how far it led, in decibels.
    lead_db: float | None
    #: None when no voices have been found in what it won.
    voice: OneVoice | None


def wear(take_name: str, match: str, who: str | None, *, new: bool = False, kind: str = "person",
         ) -> tuple[str, str | None]:
    """Say that `who` wore one recording of a take, or with None that nobody did.

    `match` is a regular expression for the recording's label or path. Returns
    the recording's name and the person's.
    """
    from ..speakers import names

    take = find(take_name)
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, coalesce(ta.label, a.path), a.has_audio
             FROM media.take_assets ta JOIN media.assets a ON a.id = ta.asset_id
            WHERE ta.take_id = %s AND (ta.label ~* %s OR a.path ~* %s)
            ORDER BY ta.label, a.path""",
        (take.id, match, match),
    )
    found = cur.fetchall()
    if not found:
        raise TakeError(f"no recording of {take.name} matches {match!r}; `media takes {take.name}` shows them")
    if len(found) > 1:
        some = ", ".join(_name(label) for _, label, _ in found[:5])
        raise TakeError(f"{len(found)} recordings of {take.name} match {match!r} "
                        f"({some}{', ...' if len(found) > 5 else ''}); say which one")
    ((asset_id, label, has_audio),) = found
    if not has_audio:
        raise TakeError(f"{_name(label)} has no sound, so nobody wore it")
    # Nobody is added for a recording that was not there to be worn.
    with db.transaction():
        persona_id = None if who is None else names.person(who, new=new, kind=kind)
        cur = db.cursor()
        cur.execute("UPDATE media.take_assets SET persona_id = %s WHERE take_id = %s AND asset_id = %s",
                    (persona_id, take.id, asset_id))
        cur.execute("SELECT name FROM media.personas WHERE id = %s", (persona_id,))
        person = cur.fetchone()
    return _name(label), person[0] if person else None


def worn(take: Take) -> list[Microphone]:
    """A take's microphones: each placed recording with sound that is a worn one, or that somebody is said to wear."""
    cur = db.cursor()
    cur.execute(
        """SELECT ta.asset_id, p.id, p.name
             FROM media.take_assets ta JOIN media.personas p ON p.id = ta.persona_id
            WHERE ta.take_id = %s""",
        (take.id,),
    )
    wearers = {asset_id: (persona_id, name) for asset_id, persona_id, name in cur.fetchall()}
    return [Microphone(member.asset_id, _name(member.label), member.source, float(member.offset_s),
                       *wearers.get(member.asset_id, (None, None)))
            for member in members(take.id)
            if member.placed and member.has_audio and (member.role == "lav" or member.asset_id in wearers)]


def loud_level(level: Any) -> float:
    """What a microphone is measured against: the level it is under nineteen twentieths of the time."""
    import numpy as np

    return float(np.percentile(level, 95)) if len(level) else 0.0


def loudest(heard: Sequence[tuple[Any, float]], spans: Sequence[tuple[float, float]], *,
            margin_db: float = MARGIN_DB, floor_db: float = FLOOR_DB) -> list[Choice]:
    """For each stretch of take time, which microphone heard it loudest.

    `heard` is each microphone's level, `RATE` values a second in its own
    time, and its offset. A microphone that recorded nothing is never chosen.
    """
    import numpy as np

    loud = [loud_level(level) for level, _ in heard]
    chosen = []
    for start, end in spans:
        found = []
        for index, (level, offset) in enumerate(heard):
            if loud[index] < SILENT:
                continue
            first = max(0, round((start - offset) * RATE))
            last = min(len(level), round((end - offset) * RATE))
            if last - first < SHORTEST_S * RATE:
                continue
            here = float(np.sqrt((np.asarray(level[first:last], dtype=np.float64) ** 2).mean()))
            found.append((20.0 * math.log10(max(here, 1e-9) / loud[index]), index))
        if len(found) < 2:
            chosen.append(Choice())
            continue
        found.sort(reverse=True)
        (top, index), (second, _) = found[:2]
        sure = top - second >= margin_db and top >= floor_db
        chosen.append(Choice(index if sure else None, top - second, top))
    return chosen


def read(take: Take, *, margin_db: float = MARGIN_DB, floor_db: float = FLOOR_DB) -> Reading:
    """Listen to a take's microphones over each of its transcribed lines. Writes nothing."""
    microphones = worn(take)
    if len(microphones) < 2:
        raise TakeError(
            f"{take.name} has {len(microphones)} microphones placed, and telling who spoke takes two or more; "
            "`media sync` places a recording, and `media wear` says a recording is somebody's microphone")
    heard, silent = [], []
    for microphone in microphones:
        if not Path(microphone.source).exists():
            raise TakeError(f"{microphone.name} is not where it was: {microphone.source}")
        found = levels(microphone.source)
        level = found[0] if found else []
        if loud_level(level) < SILENT:
            silent.append(microphone.name)
        heard.append((level, microphone.offset_s))

    cur = db.cursor()
    cur.execute(
        """SELECT s.id, s.asset_id, s.start_s + ta.offset_s, s.end_s + ta.offset_s,
                  -- A name this gave the line last time is not a person's: it is about to be given again.
                  CASE WHEN jsonb_exists(a.meta #> ARRAY[%s::text, 'lines', s.persona_id::text], s.id::text)
                       THEN NULL ELSE s.persona_id END,
                  c.label, c.persona_id, cp.name
             FROM media.take_assets ta
             JOIN media.assets a ON a.id = ta.asset_id
             JOIN media_video.segments s ON s.asset_id = ta.asset_id
             LEFT JOIN media_video.speaker_clusters c ON c.id = s.cluster_id
             LEFT JOIN media.personas cp ON cp.id = c.persona_id
            WHERE ta.take_id = %s AND (ta.is_primary OR ta.offset_method IS NOT NULL)
            ORDER BY 3, 4, a.path""",
        (_NOTED, take.id),
    )
    rows = cur.fetchall()
    choices = loudest(heard, [(float(start), float(end)) for _, _, start, end, *_ in rows],
                      margin_db=margin_db, floor_db=floor_db)
    lines = [Line(segment_id, asset_id, float(start), float(end),
                  None if choice.winner is None else microphones[choice.winner], choice.lead_db,
                  named_id, voice, voice_owner_id, voice_owner)
             for (segment_id, asset_id, start, end, named_id, voice, voice_owner_id, voice_owner), choice
             in zip(rows, choices)]
    return Reading(take, microphones, lines, margin_db, silent)


def write(reading: Reading) -> int:
    """Give each line its microphone's wearer. Returns how many lines were given one.

    What an earlier reading gave is taken back first, so the take is left as
    this reading alone would leave it. Lines a person named are not touched.
    """
    given: dict[Any, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for line in reading.lines:
        if line.outcome == GIVEN:
            given[line.asset_id][str(line.microphone.wearer_id)].append(str(line.segment_id))
    with db.transaction():
        cur = db.cursor()
        cur.execute(
            """UPDATE media_video.segments s SET persona_id = NULL
                 FROM media.take_assets ta
                 JOIN media.assets a ON a.id = ta.asset_id
                WHERE ta.take_id = %s AND s.asset_id = a.id
                  AND jsonb_exists(a.meta #> ARRAY[%s::text, 'lines', s.persona_id::text], s.id::text)""",
            (reading.take.id, _NOTED),
        )
        cur.execute(
            """UPDATE media.assets a SET meta = a.meta - %s
                 FROM media.take_assets ta
                WHERE ta.take_id = %s AND ta.asset_id = a.id AND jsonb_exists(a.meta, %s)""",
            (_NOTED, reading.take.id, _NOTED),
        )
        for asset_id, lines in given.items():
            cur.execute(
                """UPDATE media_video.segments s SET persona_id = g.persona
                     FROM jsonb_to_recordset(%s) AS g(segment uuid, persona uuid)
                    WHERE s.id = g.segment AND s.persona_id IS NULL""",
                (db.jsonb([{"segment": segment, "persona": persona}
                           for persona, segments in lines.items() for segment in segments]),),
            )
            cur.execute(
                "UPDATE media.assets SET meta = meta || %s WHERE id = %s",
                (db.jsonb({_NOTED: {"take": reading.take.name, "margin_db": reading.margin_db, "lines": lines}}),
                 asset_id),
            )
    return sum(len(segments) for lines in given.values() for segments in lines.values())


def won(reading: Reading) -> list[Won]:
    """What each microphone won, and whether one voice says it."""
    cur = db.cursor()
    cur.execute(
        """SELECT a.id, coalesce(ta.label, a.path)
             FROM media.take_assets ta JOIN media.assets a ON a.id = ta.asset_id WHERE ta.take_id = %s""",
        (reading.take.id,),
    )
    recordings = {asset_id: _name(label) for asset_id, label in cur.fetchall()}
    found = []
    for microphone in reading.microphones:
        its = [line for line in reading.lines if line.microphone is microphone]
        # Voices are found a recording at a time, so they are counted a recording at a time.
        said: dict[Any, Counter[tuple[str, str | None]]] = defaultdict(Counter)
        for line in its:
            if line.voice is not None:
                said[line.asset_id][(line.voice, line.voice_owner)] += line.end_s - line.start_s
        voice = None
        if said:
            # The recording that heard the most of it has the most to say.
            asset_id, voices = max(said.items(), key=lambda one: (sum(one[1].values()), str(one[0])))
            (label, owner), most = max(voices.items(), key=lambda one: (one[1], one[0][0]))
            voice = OneVoice(label, recordings.get(asset_id, ""), most / sum(voices.values()), owner)
        leads = [line.lead_db for line in its if line.lead_db is not None]
        found.append(Won(microphone, len(its), sum(line.end_s - line.start_s for line in its),
                           median(leads) if leads else None, voice))
    return found


def folder(take: Take, settings: Settings | None = None) -> Path:
    """Where a take's listening clips are kept."""
    from ..speakers.names import slug

    settings = settings or Settings.from_env()
    return settings.work_dir / "microphones" / (slug(take.name) or str(take.id)[:8])


def clips(reading: Reading, settings: Settings | None = None) -> dict[Any, Path]:
    """Cut, for each microphone, a few moments of what it won, to listen to. Returns them by recording.

    The moments are the lines it won most clearly, taken from the whole length
    of the take and not its first minute, in the order they were said, out of
    that microphone's own recording. A microphone that won nothing clearly
    enough gets no clip.
    """
    import shutil

    from ..speakers import cards
    from ..speakers.names import slug

    into = folder(reading.take, settings)
    if into.is_dir():
        shutil.rmtree(into)
    made = {}
    for microphone in reading.microphones:
        moments: list[float] = []
        for line in reading.lines:
            if (line.microphone is not microphone or (line.lead_db or 0.0) < CLIP_LEAD_DB
                    or line.end_s - line.start_s < CLIP_MOMENT_S):
                continue
            # In the microphone's own time. A line is in more than one
            # transcript, and is wanted once.
            start = (line.start_s + line.end_s - CLIP_MOMENT_S) / 2 - microphone.offset_s
            if start >= 0 and (not moments or start >= moments[-1] + CLIP_MOMENT_S):
                moments.append(start)
        if not moments:
            continue
        if len(moments) > CLIP_MOMENTS:
            step = (len(moments) - 1) / (CLIP_MOMENTS - 1)
            moments = [moments[round(n * step)] for n in range(CLIP_MOMENTS)]
        name = slug(Path(microphone.name).stem) or "microphone"
        made[microphone.asset_id] = cards.cut(
            microphone.source, moments, into / f"{name}-{str(microphone.asset_id)[:8]}.wav", length_s=CLIP_MOMENT_S)
    return made


def _name(label: str) -> str:
    """A recording as it is shown: its label, or its file's name."""
    return Path(label).name
