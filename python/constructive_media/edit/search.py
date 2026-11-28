"""Ask the recordings a question and get back passages, or a cut.

Everything else here needs you to know where the material is: a take, a time,
a file. What an editor asks is "where does anyone talk about the winter the
boats came in close", and that is about meaning. So every passage is kept as
an embedding, a list of numbers that is near another's when the two mean much
the same, and a question is answered by the passages nearest it.

- **A hit is a clip.** A passage already knows its take and its times, so
  what is found can be assembled as it stands: `cut` goes from a question to
  a stored cut.
- **It is one query.** The embeddings are in the same database as everything
  else, so "about the storm, finishes its sentence, not something I threw
  out" is asked at once and not agreed between two systems.
- **Being on the subject is not being good.** A passage can be exactly what
  was asked for and unusable. How near it is counts for `relevance` of the
  ranking and how good it is for the rest.
- **Words that have changed are not searched.** An embedding is kept with a
  mark of the text it was made from. Correct a transcript and that passage
  drops out of search until it is indexed again, instead of answering with
  words nobody said. Embeddings from another model are left out the same way.
- **What someone asked not to be used is not sent to a model, and is never
  found.**
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import db, text
from ..settings import Settings
from ..text import Embedder
from . import EditError, assemble, flags
from .assemble import Assembled, Style

#: How many numbers the library keeps for a passage. A model has to give this many.
DIMENSIONS = 1024
#: A passage with less text than this says too little to be found by.
LEAST_TEXT = 40
#: How much of a passage is embedded.
MOST_TEXT = 2000

_MARK = "substring(encode(sha256(convert_to(coalesce(p.text, ''), 'UTF8')), 'hex') from 1 for 16)"


@dataclass(frozen=True)
class Indexed:
    """What a round of indexing did."""
    considered: int
    embedded: int
    model: str


@dataclass(frozen=True)
class Hit:
    """A passage a search found."""
    passage_id: Any
    take: str
    start_s: float
    end_s: float
    text: str
    #: How near the question it is, 0 to 1.
    similarity: float
    #: How good it is by a recipe, or how well-formed where nothing ranks it.
    quality: float
    #: The two weighed together, and with whether it answers once a model has said.
    score: float
    ends_complete: bool = False
    #: Whether it answers the question, 0 to 1, and why, once a model has been asked.
    answers: float | None = None
    why: str = ""
    take_id: Any = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class Found:
    hits: list[Hit]
    model: str
    #: Near enough to be listed, but holding something someone asked not to be used.
    withheld: int = 0
    #: Passages in scope that could not be found because they are not indexed as they now read.
    unindexed: int = 0


def mark(said: str | None) -> str:
    """A short mark of a text, which changes when the text does."""
    return hashlib.sha256((said or "").encode()).hexdigest()[:16]


def _vector(numbers: list[float]) -> str:
    return "[" + ",".join(repr(float(number)) for number in numbers) + "]"


def waiting(model: str, project: str | None = None, take: str | None = None) -> list[tuple[Any, str]]:
    """The passages `model` has no embedding of as they now read: `(id, text)`, oldest first."""
    cur = db.cursor()
    cur.execute(
        f"""SELECT p.id, p.text
              FROM media_video.passages p
              JOIN media.takes t ON t.id = p.take_id
              LEFT JOIN media_video.passage_embeddings e ON e.passage_id = p.id
             WHERE (%s::text IS NULL OR t.project = %s) AND (%s::text IS NULL OR t.name = %s)
               AND length(p.text) > {LEAST_TEXT}
               AND (e.passage_id IS NULL OR e.model <> %s OR e.text_hash <> {_MARK})
             ORDER BY p.created_at, p.id""",
        (project, project, take, take, model),
    )
    rows = cur.fetchall()
    held = flags.held_back(rows)
    return [(passage_id, said) for passage_id, said in rows if str(passage_id) not in held]


def index(
    project: str | None = None, take: str | None = None, *, batch: int = 32, limit: int | None = None,
    by: Embedder | None = None, settings: Settings | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Indexed:
    """Embed every passage that has no embedding of its words as they now are.

    Each batch is stored as it arrives, so a round that stops has lost
    nothing. A model that gives a different count of numbers from the one the
    library keeps is refused before anything of its is stored: numbers from
    another space would still compare, and mean nothing.
    """
    reader = by or text.embedder(settings=settings)
    rows = waiting(reader.model, project, take)[:limit]
    cur = db.cursor()
    embedded = 0
    for at in range(0, len(rows), max(batch, 1)):
        some = rows[at:at + max(batch, 1)]
        made = reader.embed([" ".join(said.split())[:MOST_TEXT] for _, said in some])
        if len(made) != len(some):
            raise EditError(f"{reader.model} was given {len(some)} passages and answered for {len(made)}")
        for numbers in made:
            if len(numbers) != DIMENSIONS:
                raise EditError(f"{reader.model} gives {len(numbers)} numbers for a text and the library keeps "
                                f"{DIMENSIONS}; choose an embedding model that gives {DIMENSIONS}")
        cur.execute(
            """INSERT INTO media_video.passage_embeddings (passage_id, embedding, model, text_hash)
               SELECT r.passage_id, r.embedding::vector, %s, r.text_hash
                 FROM jsonb_to_recordset(%s) AS r(passage_id uuid, embedding text, text_hash text)
               ON CONFLICT (passage_id) DO UPDATE SET
                 embedding = EXCLUDED.embedding, model = EXCLUDED.model,
                 text_hash = EXCLUDED.text_hash, computed_at = now()""",
            (reader.model, db.jsonb([
                {"passage_id": str(passage_id), "embedding": _vector(numbers), "text_hash": mark(said)}
                for (passage_id, said), numbers in zip(some, made, strict=True)])),
        )
        embedded += len(some)
        if progress is not None:
            progress(embedded, len(rows))
    return Indexed(len(rows), embedded, reader.model)


def search(
    question: str, *, project: str | None = None, take: str | None = None, top: int = 20,
    relevance: float = 0.65, min_coherence: float = 0.5, recipe: str = "interview",
    min_len_s: float | None = None, max_len_s: float | None = None, include_cut: bool = False,
    by: Embedder | None = None, settings: Settings | None = None,
) -> Found:
    """The passages that best answer to `question`, best first.

    `relevance` is how much of the ranking is how near the question a passage
    is, against how good it is by `recipe`. At 1 the list is the most on the
    subject, usable or not; at 0 it is the best passages whatever was asked.
    Each moment appears once, and anything cut for good is left out unless
    `include_cut`.
    """
    if not question.strip():
        raise EditError("a search needs a question")
    if not 0.0 <= relevance <= 1.0:
        raise EditError("relevance is a share, from 0 to 1")
    reader = by or text.embedder(settings=settings)
    (asked,) = reader.embed([" ".join(question.split())], asking=True)
    if len(asked) != DIMENSIONS:
        raise EditError(f"{reader.model} gives {len(asked)} numbers for a text and the library keeps {DIMENSIONS}")
    near = _vector(asked)
    cur = db.cursor()
    cur.execute(
        f"""SELECT s.passage_id, s.take, s.start_s, s.end_s, s.text, s.ends_complete,
                   1 - (s.embedding <=> %s::vector), s.coherence, s.take_id
              FROM media_video.v_searchable s
              JOIN media_video.passages p ON p.id = s.passage_id
              JOIN media_video.passage_embeddings e ON e.passage_id = s.passage_id
             WHERE e.model = %s AND e.text_hash = {_MARK}
               AND s.coherence >= %s AND (%s OR NOT s.excluded)
               AND (%s::text IS NULL OR s.project = %s) AND (%s::text IS NULL OR s.take = %s)
               AND (%s::float8 IS NULL OR s.duration_s >= %s) AND (%s::float8 IS NULL OR s.duration_s <= %s)
             ORDER BY s.embedding <=> %s::vector
             LIMIT %s""",
        (near, reader.model, min_coherence, include_cut, project, project, take, take,
         min_len_s, min_len_s, max_len_s, max_len_s, near, max(top, 1) * 4),
    )
    rows = cur.fetchall()
    # What the recipe makes of the few that were found is asked apart. Joined to the search, the scores of
    # every passage were added up again for each row of it, and a library of any size took minutes.
    cur.execute(
        """SELECT subject_id, score FROM media_video.v_combined
            WHERE subject_type = 'passage' AND recipe = %s AND subject_id = ANY(%s::uuid[])""",
        (recipe, [str(row[0]) for row in rows]),
    )
    by_recipe = {str(passage_id): score for passage_id, score in cur.fetchall()}
    held = flags.held_back((row[0], row[4]) for row in rows)
    hits: list[Hit] = []
    withheld = 0
    for passage_id, within, start, end, said, complete, similarity, coherence, take_id in rows:
        if str(passage_id) in held:
            withheld += 1
            continue
        quality = by_recipe.get(str(passage_id))
        quality = coherence if quality is None else quality
        similarity = max(0.0, min(1.0, float(similarity)))
        quality = max(0.0, min(1.0, float(quality if quality is not None else 0.5)))
        hits.append(Hit(passage_id, within, float(start), float(end), said or "", round(similarity, 4),
                        round(quality, 4), round(relevance * similarity + (1 - relevance) * quality, 4),
                        bool(complete), take_id=take_id))
    hits.sort(key=lambda hit: (-hit.score, hit.take, hit.start_s))
    # Passages found in different ways cover the same moments. The best of each is enough.
    chosen: list[Hit] = []
    for hit in hits:
        if not any(other.take_id == hit.take_id
                   and min(hit.end_s, other.end_s) - max(hit.start_s, other.start_s)
                   > 0.3 * min(hit.duration_s, other.duration_s) for other in chosen):
            chosen.append(hit)
    return Found(chosen[:top], reader.model, withheld, len(waiting(reader.model, project, take)))


def cut(
    question: str, name: str, *, target_s: float = 90.0, chronological: bool = True, shape: Style | None = None,
    project: str | None = None, take: str | None = None, title: str | None = None,
    by: Embedder | None = None, settings: Settings | None = None, **options: Any,
) -> Assembled:
    """Answer a question with a cut: the best of what was found, up to `target_s`, stored as `name`.

    The passages are used whole, each seen and heard through the right
    recording as in any other assembly, in the order they were said or, with
    `chronological` off, best first.
    """
    found = search(question, project=project, take=take, top=60, by=by, settings=settings, **options)
    if not found.hits:
        raise EditError("nothing that is indexed answers to that"
                        + (f"; {found.unindexed} passages are not indexed yet" if found.unindexed else ""))
    chosen: list[Hit] = []
    total = 0.0
    for hit in found.hits:
        if total + hit.duration_s > target_s:
            continue
        chosen.append(hit)
        total += hit.duration_s
        if total >= 0.95 * target_s:
            break
    if not chosen:
        raise EditError(f"everything that answers to that is longer than {target_s:g} seconds")
    if chronological:
        chosen.sort(key=lambda hit: (hit.take, hit.start_s))
    return assemble.interview(name, shape, only=[hit.passage_id for hit in chosen], project=project,
                              title=title or " ".join(question.split()))
