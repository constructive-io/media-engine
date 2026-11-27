"""A ranker taught from preferences: which of two shots a person chose.

The model is the smallest thing that can work. Each shot is a handful of
measurements, and the chance that one shot is chosen over another is a
logistic function of the difference between theirs. It needs hundreds of
choices where anything larger needs tens of thousands, and what it learned
can be read: more of this was liked, more of that was not.

How well it does is measured on footage it was not taught from. Shots from
one shoot share a lens, a light and a place, so a model tested on shots whose
neighbours it has seen reports a figure it will not reach on anything new.
Whole shoots are held out in turn. A library that is all one shoot holds out
whole files, and says so. The figure is read against one half, which is what
tossing a coin gets, and a model that has not shown it does better than that
by more than chance is stored with its figures and never used.

What a model makes of a shot is written down as a score called `preferred`:
the chance the shot would be chosen over the middle shot of everything the
model covers. A recipe that weighs `preferred` ranks by it, next to whatever
else it weighs.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from .. import db
from . import EditError, scores

#: What a shot is known by. Each is a column of `media_video.v_shot_features`,
#: so a model is taught from and used on the same figures.
FEATURES = ("duration_s", "sharpness", "exposure", "motion", "lift_db", "loudness", "subject_n",
            "subject_area", "shot_size_n", "sat_mean", "hue_warm", "hue_cool", "hue_green", "person_boxes")
#: The score a model writes, and who it is written by.
SCORE = "preferred"
PRODUCER = "ranker:"
#: The fewest choices a model is fitted to, here and inside each test.
FEWEST = 10
#: The most rounds of holding out. Past this, shoots or files are held out several at a time.
ROUNDS = 10
#: How sure a model has to be that it beats a coin: one chance in twenty of a fluke.
SURE = 1.645


@dataclass(frozen=True)
class Example:
    """One choice: the measurements of the shot chosen and of the one passed over."""

    won: tuple[float, ...]
    lost: tuple[float, ...]
    shoots: frozenset[str]
    files: frozenset[str]


@dataclass(frozen=True)
class Fold:
    held_out: str
    pairs: int
    accuracy: float


@dataclass(frozen=True)
class Model:
    name: str
    features: tuple[str, ...]
    #: How far one spread of each feature moves a choice. Above zero, more is liked.
    theta: tuple[float, ...]
    #: The spread of each feature, between the two shots of a choice.
    scale: tuple[float, ...]
    pairs: int
    #: Share of the choices it was fitted to that it gets right. It has seen these.
    fitted: float
    #: Share it gets right of choices over footage it was not fitted to. None when that could not be tested.
    accuracy: float | None = None
    tested: int = 0
    #: What was held out to test it: `shoot` or `file`.
    held_out_by: str | None = None
    folds: tuple[Fold, ...] = ()
    scope: str | None = None
    match: str | None = None
    actor: str | None = None
    source: str | None = None

    @property
    def proven(self) -> bool:
        """Whether it beat a coin by more than chance on footage it had not seen."""
        return self.accuracy is not None and self.accuracy - 0.5 > SURE * math.sqrt(0.25 / self.tested)

    @property
    def weights(self) -> list[tuple[str, float]]:
        """What it goes by, strongest first."""
        return sorted(zip(self.features, self.theta, strict=True), key=lambda item: -abs(item[1]))

    def strength(self, measured: Sequence[float]) -> float:
        """Where a shot stands. The gap between two shots is the log odds of the choice."""
        return sum(weight * value / spread
                   for weight, value, spread in zip(self.theta, measured, self.scale, strict=True))


def examples(*, source: str | None = "human", actor: str | None = None, match: str | None = None) -> list[Example]:
    """The choices a model can be taught from.

    A pair one person has ruled on more than once counts as what they said
    last. `match` keeps to choices between shots of files whose paths match.
    """
    won = ", ".join(f"(p.w).{feature}" for feature in FEATURES)
    lost = ", ".join(f"(p.l).{feature}" for feature in FEATURES)
    pair = "least(p.won_shot, p.lost_shot), greatest(p.won_shot, p.lost_shot)"
    cur = db.cursor()
    cur.execute(
        f"""SELECT DISTINCT ON (p.actor, {pair})
                   {won}, {lost}, p.won_shoot, p.lost_shoot, (p.w).path, (p.l).path
              FROM media_video.v_preference_pairs p
             WHERE (%s::text IS NULL OR p.source_of = %s)
               AND (%s::text IS NULL OR p.actor = %s)
               AND (%s::text IS NULL OR ((p.w).path ~* %s AND (p.l).path ~* %s))
             ORDER BY p.actor, {pair}, p.decided_at DESC, p.decision_id DESC""",
        (source, source, actor, actor, match, match, match),
    )
    count = len(FEATURES)
    return [Example(tuple(map(float, row[:count])), tuple(map(float, row[count:2 * count])),
                    frozenset(row[2 * count:2 * count + 2]), frozenset(row[2 * count + 2:]))
            for row in cur.fetchall()]


def fit(differences: Sequence[Sequence[float]], *, penalty: float = 0.01, rounds: int = 50) -> list[float]:
    """The weights that best explain a set of choices.

    Each row is the chosen shot's measurements less the other's. There is no
    constant term: a choice has no lean of its own, and swapping the two
    shots must swap the answer. The fit is by Newton's method, which reaches
    the one best answer in a few steps. `penalty` holds weights near zero so
    that a feature which happens to split a small set cleanly does not run
    away with it.
    """
    count, width = len(differences), len(differences[0])
    theta = [0.0] * width

    def cost(weights: Sequence[float]) -> float:
        return (sum(_softplus(-_dot(row, weights)) for row in differences) / count
                + penalty / 2 * sum(weight * weight for weight in weights))

    standing = cost(theta)
    for _ in range(rounds):
        slope = [penalty * weight for weight in theta]
        curve = [[penalty if across == down else 0.0 for across in range(width)] for down in range(width)]
        for row in differences:
            wrong = _sigmoid(-_dot(row, theta))
            give = wrong * (1.0 - wrong) / count
            for down in range(width):
                if not row[down]:
                    continue
                slope[down] -= wrong * row[down] / count
                line, scaled = curve[down], give * row[down]
                for across in range(down + 1):
                    line[across] += scaled * row[across]
        step = _solve(curve, slope)
        # A full step is right once the answer is near. Far from it, it can overshoot.
        reach = 1.0
        while reach > 1e-6:
            tried = [weight - reach * move for weight, move in zip(theta, step, strict=True)]
            if (found := cost(tried)) <= standing:
                break
            reach /= 2
        else:
            break
        moved, theta, standing = max(abs(reach * move) for move in step), tried, found
        if moved < 1e-9:
            break
    return theta


def train(
    name: str, *, source: str | None = "human", actor: str | None = None, match: str | None = None,
    scope: str | None = None, min_pairs: int = 30, penalty: float = 0.01,
) -> Model:
    """Fit a model to the choices on record and test it on footage held out.

    Nothing is stored: `save` does that. A model is not fitted to fewer than
    `min_pairs` choices, since one fitted to a handful gets all of them right
    and has learned nothing.
    """
    found = examples(source=source, actor=actor, match=match)
    if len(found) < max(min_pairs, FEWEST):
        raise EditError(
            f"{len(found)} choices between shots are on record"
            + (f" matching {match!r}" if match else "")
            + f", and a model needs {max(min_pairs, FEWEST)}. `media pairs` lists pairs worth choosing between"
        )
    theta, scale = _fitted(found, penalty)
    held_out_by, folds, accuracy, tested = "shoot", *_held_out(found, lambda example: example.shoots, penalty)
    if not tested:
        held_out_by, (folds, accuracy, tested) = "file", _held_out(found, lambda example: example.files, penalty)
    return Model(
        name, FEATURES, tuple(theta), tuple(scale), len(found), _accuracy(theta, scale, found),
        accuracy, tested, held_out_by if tested else None, tuple(folds), scope, match, actor, source,
    )


def save(model: Model) -> None:
    """Store a model, in place of any of the same name, with how it did."""
    cur = db.cursor()
    cur.execute(
        """INSERT INTO media_video.models
             (name, scope, train_match, train_actor, features, theta, scale, n_pairs, metrics, trained_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())
           ON CONFLICT (name) DO UPDATE SET
             scope = EXCLUDED.scope, train_match = EXCLUDED.train_match, train_actor = EXCLUDED.train_actor,
             features = EXCLUDED.features, theta = EXCLUDED.theta, scale = EXCLUDED.scale,
             n_pairs = EXCLUDED.n_pairs, metrics = EXCLUDED.metrics, trained_at = EXCLUDED.trained_at""",
        (model.name, model.scope, model.match, model.actor, list(model.features), list(model.theta),
         list(model.scale), model.pairs, db.jsonb({
             "fitted": model.fitted, "accuracy": model.accuracy, "tested": model.tested,
             "held_out_by": model.held_out_by, "source": model.source,
             "folds": [[fold.held_out, fold.pairs, fold.accuracy] for fold in model.folds],
         })),
    )


_STORED = """SELECT name, features, theta, scale, n_pairs, metrics, scope, train_match, train_actor
               FROM media_video.models WHERE kind = 'shot_ranker'"""


def load(name: str) -> Model:
    cur = db.cursor()
    cur.execute(_STORED + " AND name = %s", (name,))
    if not (found := cur.fetchone()):
        raise EditError(f"no model is called {name!r}; `media models` lists them")
    return _model(*found)


def listing() -> list[Model]:
    """Every stored model, the latest first."""
    cur = db.cursor()
    cur.execute(_STORED + " ORDER BY trained_at DESC, name")
    return [_model(*row) for row in cur.fetchall()]


def forget(name: str) -> None:
    """Delete a model and the scores it wrote."""
    load(name)
    with db.transaction():
        cur = db.cursor()
        cur.execute("DELETE FROM media_video.scores WHERE name = %s AND producer = %s", (SCORE, PRODUCER + name))
        cur.execute("DELETE FROM media_video.models WHERE name = %s", (name,))


def apply(model: Model) -> int:
    """Write what a model makes of every shot it covers, in place of what it wrote before.

    A model that has not proven itself writes nothing, and what an earlier
    fit under the same name wrote is taken away.
    """
    with db.transaction():
        cur = db.cursor()
        cur.execute("DELETE FROM media_video.scores WHERE name = %s AND producer = %s",
                    (SCORE, PRODUCER + model.name))
        if not model.proven:
            return 0
        cur.execute(
            f"""SELECT shot_id, {', '.join(model.features)} FROM media_video.v_shot_features
                 WHERE (%s::text IS NULL OR path ~* %s)""",
            (model.match, model.match),
        )
        standing = [(shot_id, model.strength(tuple(map(float, measured)))) for shot_id, *measured in cur.fetchall()]
        if not standing:
            return 0
        middle = median(strength for _, strength in standing)
        written = scores.record(
            ("shot", shot_id, SCORE, _sigmoid(strength - middle), strength, scores.JUDGED, "cross",
             PRODUCER + model.name)
            for shot_id, strength in standing)
        return written[SCORE]


def refresh() -> Counter[str]:
    """Bring every model's scores up to date with the shots there are now."""
    written = sum(apply(model) for model in listing())
    return Counter({SCORE: written} if written else {})


def _model(
    name: str, features: Sequence[str], theta: Sequence[float], scale: Sequence[float], pairs: int | None,
    metrics: dict[str, Any], scope: str | None, match: str | None, actor: str | None,
) -> Model:
    return Model(
        name, tuple(features), tuple(theta), tuple(scale), pairs or 0, float(metrics.get("fitted") or 0.0),
        metrics.get("accuracy"), int(metrics.get("tested") or 0), metrics.get("held_out_by"),
        tuple(Fold(*fold) for fold in metrics.get("folds") or ()), scope, match, actor, metrics.get("source"),
    )


def _fitted(found: Sequence[Example], penalty: float) -> tuple[list[float], list[float]]:
    """Weights for a set of choices, and the spread each feature was measured against.

    Each feature is put on its own spread first, so that a weight says how
    much a feature matters and not what unit it was measured in.
    """
    apart = [[one - other for one, other in zip(example.won, example.lost, strict=True)] for example in found]
    scale = []
    for column in zip(*apart, strict=True):
        middle = sum(column) / len(column)
        spread = math.sqrt(sum((value - middle) ** 2 for value in column) / len(column))
        scale.append(spread if spread > 1e-9 else 1.0)
    return fit([[value / spread for value, spread in zip(row, scale, strict=True)] for row in apart],
               penalty=penalty), scale


def _right(theta: Sequence[float], scale: Sequence[float], example: Example) -> bool:
    return sum(weight * (one - other) / spread for weight, one, other, spread
               in zip(theta, example.won, example.lost, scale, strict=True)) > 0


def _accuracy(theta: Sequence[float], scale: Sequence[float], found: Sequence[Example]) -> float:
    return sum(_right(theta, scale, example) for example in found) / len(found)


def _held_out(
    found: Sequence[Example], groups: Callable[[Example], frozenset[str]], penalty: float,
) -> tuple[list[Fold], float | None, int]:
    """Test on each group in turn, fitting to the choices that do not touch it.

    A choice between shots of two groups is tested once for each, and counts
    once, as the share of those tests it passed.
    """
    names = sorted({name for example in found for name in groups(example)})
    held = [names[start::ROUNDS] for start in range(ROUNDS)] if len(names) > ROUNDS else [[name] for name in names]
    folds: list[Fold] = []
    passed: dict[int, list[bool]] = {}
    for out in held:
        apart = set(out)
        test = [index for index, example in enumerate(found) if groups(example) & apart]
        kept = [example for example in found if not groups(example) & apart]
        if not test or len(kept) < FEWEST:
            continue
        theta, scale = _fitted(kept, penalty)
        outcomes = [_right(theta, scale, found[index]) for index in test]
        folds.append(Fold(out[0] if len(out) == 1 else f"{out[0]} and {len(out) - 1} more",
                          len(test), sum(outcomes) / len(test)))
        for index, outcome in zip(test, outcomes, strict=True):
            passed.setdefault(index, []).append(outcome)
    if not passed:
        return [], None, 0
    return folds, sum(sum(tests) / len(tests) for tests in passed.values()) / len(passed), len(passed)


def _dot(row: Sequence[float], weights: Sequence[float]) -> float:
    return sum(value * weight for value, weight in zip(row, weights, strict=True))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    raised = math.exp(value)
    return raised / (1.0 + raised)


def _softplus(value: float) -> float:
    """log(1 + e^value), without overflowing."""
    return max(value, 0.0) + math.log1p(math.exp(-abs(value)))


def _solve(matrix: Sequence[Sequence[float]], right: Sequence[float]) -> list[float]:
    """Solve for x in `matrix` x = `right`, reading the lower half of a symmetric, positive matrix."""
    width = len(right)
    lower = [[0.0] * width for _ in range(width)]
    for down in range(width):
        for across in range(down + 1):
            rest = matrix[down][across] - sum(lower[down][k] * lower[across][k] for k in range(across))
            lower[down][across] = math.sqrt(rest) if down == across else rest / lower[across][across]
    half = [0.0] * width
    for down in range(width):
        half[down] = (right[down] - sum(lower[down][k] * half[k] for k in range(down))) / lower[down][down]
    solved = [0.0] * width
    for down in reversed(range(width)):
        solved[down] = (half[down] - sum(lower[k][down] * solved[k] for k in range(down + 1, width))) / lower[down][down]
    return solved
