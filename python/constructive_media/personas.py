"""Personas: the people and characters everything else is about.

A persona is reached from two sides. Listening finds a voice in a recording
and someone says whose it is; speaking gives somebody a voice to say lines in.
Both write a persona down in passing, with a name and nothing else, because
neither is about the person.

This is the place that is. `keep` writes a persona down on purpose, or adds to
one that is there: whether they are a real person or an invented character,
which decides how they may be used, and a few words about them. What is not
given is left as it was, so adding a line about somebody does not undo what
kind they are. `listing` is everybody, with how many voices each has on either
side: found, the voices in recordings that were named as theirs, and
speaking, the voices that can say a line as them. Somebody with neither is
only a name so far.

A persona is found by name or by slug, whichever is given, as it is everywhere
else.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import db
from .speakers import SpeakerError
from .speakers.names import slug

KINDS = ("person", "character")

_SELECT = """SELECT p.id, p.slug, p.name, p.kind, p.bio,
                    (SELECT count(*) FROM media_video.speaker_clusters c WHERE c.persona_id = p.id),
                    (SELECT count(*) FROM media.voices v
                      WHERE v.persona_id = p.id AND v.direction = 'synthesis')
               FROM media.personas p"""


@dataclass(frozen=True)
class Persona:
    slug: str
    name: str
    kind: str
    bio: str | None = None
    #: Voices found in recordings and named as theirs.
    found: int = 0
    #: Voices that can say a line as them.
    speaking: int = 0


def keep(who: str, *, kind: str | None = None, bio: str | None = None, new: bool = False) -> tuple[Persona, str]:
    """Write a persona down, or add to one that is there.

    Returns the persona as it now stands and what was done: `added`,
    `amended`, or `unchanged` when nothing was given that differs. Somebody
    not yet known is added only with `new`, so a name spelt wrongly is not a
    second person, and is a person unless `kind` says otherwise. A `bio` of no
    words takes the one there away.
    """
    if kind is not None and kind not in KINDS:
        raise SpeakerError(f"a persona is a {' or a '.join(KINDS)}, not {kind!r}")
    if bio is not None:
        bio = " ".join(bio.split())
    with db.transaction():
        cur = db.cursor()
        cur.execute(
            "SELECT id, kind, bio FROM media.personas WHERE slug = %s OR lower(name) = lower(%s) ORDER BY created_at",
            (slug(who), who),
        )
        found = cur.fetchall()
        if len(found) > 1:
            raise SpeakerError(f"{len(found)} people are called {who!r}; use the slug of the one you mean")
        if not found:
            if not new:
                raise SpeakerError(f"nobody is called {who!r} yet; add --new to add them")
            if not slug(who):
                raise SpeakerError(f"{who!r} has no letters or numbers to make a slug from")
            cur.execute(
                "INSERT INTO media.personas (slug, name, kind, bio) VALUES (%s, %s, %s, %s) RETURNING id",
                (slug(who), " ".join(who.split()), kind or KINDS[0], bio or None),
            )
            done = "added"
        else:
            persona_id, was_kind, was_bio = found[0]
            now_kind, now_bio = kind or was_kind, was_bio if bio is None else (bio or None)
            done = "unchanged" if (now_kind, now_bio) == (was_kind, was_bio) else "amended"
            if done == "amended":
                cur.execute("UPDATE media.personas SET kind = %s, bio = %s, updated_at = now() WHERE id = %s",
                            (now_kind, now_bio, persona_id))
        persona_id = found[0][0] if found else cur.fetchone()[0]
        cur.execute(_SELECT + " WHERE p.id = %s", (persona_id,))
        return Persona(*cur.fetchone()[1:]), done


def listing() -> list[Persona]:
    """Everybody, by name."""
    cur = db.cursor()
    cur.execute(_SELECT + " ORDER BY lower(p.name), p.slug")
    return [Persona(*row[1:]) for row in cur.fetchall()]
