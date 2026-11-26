"""A model's opinion of a passage: whether anything was said in it.

Every other score measures the recording: how sharp, how loud, how sure the
recogniser was, whether the sentence finished. None of them can tell whether
anything was said. A well-formed thank-you scores as well as a well-formed
explanation, because form is all they can see. A text model reads the words
and answers what they cannot:

    substance       does it state, explain or reveal something particular
    interest        would somebody watching want to hear it
    self_contained  does it stand up without the conversation around it
    topic           what it is about, in a few words

Three rules keep an opinion in its place:

- **It is the last thing asked, of the fewest.** Reading costs seconds a
  passage, so only the passages the cheaper scores already rank are read,
  densest first: the cheap scores reward fluent chatter, and how much is said
  in how little time is the one that does not.
- **It is a score like any other.** It is written beside the rest with the
  model's name on it, a recipe weighs it or does not, and "has this been
  read" is a query. Run again, only what has not been read is.
- **What someone asked not to be used is not sent.** A passage in which
  anyone asks that never leaves for a model, wherever the model runs.

Check a model before trusting it: one that gives filler the same marks as an
explanation adds noise with a number on it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .. import db, text
from ..settings import Settings
from ..text import Writer
from . import flags, scores

#: The scores a judgement writes.
NAMES = ("substance", "interest", "self_contained")
#: How much of a passage is read, and of one being checked against a question.
MOST_TEXT = 1800
MOST_ANSWER = 1200
#: The longest a topic may be.
TOPIC = 60
#: How much a model's "does it answer" counts beside how the passage ranked.
ANSWERS = 0.6

PROMPT = """You are helping an editor choose which passages of a recorded conversation are worth keeping.

Answer ONLY with a JSON object:
{"substance": 0.0 to 1.0, "interest": 0.0 to 1.0, "self_contained": 0.0 to 1.0, "topic": "four words at most"}

substance       Does it state, explain or reveal something particular?
                Greetings, thanks, filler and noises of agreement are 0.0.
interest        Would somebody watching want to hear it?
self_contained  Does it make sense without the conversation around it?
topic           What it is about.

PASSAGE: {text}"""

QUESTION = """Does this passage ANSWER the question? The question may be only a few words saying what is wanted, \
such as "how the bridge was paid for"; read those as the question they stand for. A passage answers if what is \
wanted is said in it. Being about the same subject is not answering.

Answer ONLY with a JSON object:
{"answers": 0.0 to 1.0, "why": "eight words at most"}

QUESTION: {question}
PASSAGE: {text}"""


@dataclass(frozen=True)
class Opinion:
    """What a model made of a passage, each from 0 to 1 where it said."""
    substance: float
    interest: float | None = None
    self_contained: float | None = None
    topic: str | None = None


@dataclass(frozen=True)
class Judged:
    """What a round of judging did."""
    considered: int
    judged: int
    #: Replies that could not be read. Those passages are asked about again next time.
    unread: int
    model: str


def opinion(reply: str | None) -> Opinion | None:
    """A model's reply as an opinion, or None when it holds none.

    A reply with no mark for substance is not an opinion: that is the one the
    others are for.
    """
    found = text.read(reply)
    if found is None or (substance := text.share(found.get("substance"))) is None:
        return None
    topic = found.get("topic")
    topic = " ".join(topic.split())[:TOPIC].rstrip() if isinstance(topic, str) else None
    return Opinion(substance, text.share(found.get("interest")), text.share(found.get("self_contained")),
                   topic or None)


def _asked(prompt: str, **parts: str) -> str:
    """A prompt with its blanks filled in, each as a quoted string and in one pass.

    What is filled in is somebody's speech, and is never read again for blanks.
    """
    return re.sub(r"\{(%s)\}" % "|".join(parts),
                  lambda blank: json.dumps(" ".join(parts[blank[1]].split()), ensure_ascii=False), prompt)


def shortlist(
    model: str, *, project: str | None = None, limit: int | None = 200, recipe: str = "interview",
    min_coverage: float = 0.5, min_len_s: float = 4.0, max_len_s: float = 40.0,
) -> list[tuple[Any, str]]:
    """The passages worth a model's time that `model` has not read: `(id, text)`.

    They are the ones `recipe` already ranks, of a length that could be used,
    most said in the least time first and then best first.
    """
    weight = sum(scores.recipe(recipe).weights.values()) or 1.0
    cur = db.cursor()
    cur.execute(
        """SELECT p.id, p.text
             FROM media_video.v_combined v
             JOIN media_video.passages p ON p.id = v.subject_id
             JOIN media.takes t ON t.id = p.take_id
            WHERE v.recipe = %s AND v.subject_type = 'passage' AND v.coverage >= %s
              AND (%s::text IS NULL OR t.project = %s)
              AND p.end_s - p.start_s BETWEEN %s AND %s
              AND length(p.text) > 60
              AND NOT EXISTS (SELECT 1 FROM media_video.scores s
                               WHERE s.subject_type = 'passage' AND s.subject_id = p.id
                                 AND s.name = 'substance' AND s.producer = %s)
            ORDER BY length(p.text) / (p.end_s - p.start_s) DESC, v.score DESC, p.id""",
        (recipe, min_coverage * weight, project, project, min_len_s, max_len_s, model),
    )
    rows = cur.fetchall()
    held = flags.held_back(rows)
    return [(passage_id, said) for passage_id, said in rows if str(passage_id) not in held][:limit]


def run(
    project: str | None = None, *, limit: int | None = 200, recipe: str = "interview", min_coverage: float = 0.5,
    by: Writer | None = None, settings: Settings | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Judged:
    """Have a model read the shortlist, and write what it made of each passage.

    Each opinion is stored as it arrives, so a round that is stopped, or whose
    model stops answering, has lost nothing and carries on when run again.
    """
    reader = by or text.writer(settings=settings)
    chosen = shortlist(reader.model, project=project, limit=limit, recipe=recipe, min_coverage=min_coverage)
    judged = unread = 0
    cur = db.cursor()
    for at, (passage_id, said) in enumerate(chosen, 1):
        found = opinion(reader.write(_asked(PROMPT, text=(said or "")[:MOST_TEXT]), limit=200, as_json=True))
        if found is None:
            unread += 1
        else:
            scores.record(("passage", passage_id, name, getattr(found, name), None, scores.JUDGED, "text", reader.model)
                          for name in NAMES if getattr(found, name) is not None)
            if found.topic:
                cur.execute("UPDATE media_video.passages SET scores = scores || %s WHERE id = %s",
                            (db.jsonb({"topic": found.topic, "judged_by": reader.model}), passage_id))
            judged += 1
        if progress is not None:
            progress(at, len(chosen))
    return Judged(len(chosen), judged, unread, reader.model)


def answering(
    question: str, hits: Sequence[Any], *, top: int = 10, by: Writer | None = None,
    settings: Settings | None = None,
) -> list[Any]:
    """The best `top` of a search's hits, in order of whether they answer the question.

    A search finds what is about a subject. Asked for the hardest thing
    somebody built, it will offer a passage that only has the word "built" in
    it, because being about something and answering a question differ in
    more than vocabulary. So a model is asked, of each, whether it answers.
    A reply that cannot be read counts as no.
    """
    reader = by or text.writer(settings=settings)
    asked = []
    for hit in hits[:top]:
        found = text.read(reader.write(_asked(QUESTION, question=question, text=hit.text[:MOST_ANSWER]),
                                       limit=120, as_json=True)) or {}
        answers = text.share(found.get("answers"))
        why = found.get("why")
        asked.append(replace(
            hit, answers=answers, why=" ".join(why.split())[:TOPIC] if isinstance(why, str) else "",
            score=round(ANSWERS * (answers or 0.0) + (1 - ANSWERS) * hit.score, 4)))
    return sorted(asked, key=lambda hit: -hit.score)
