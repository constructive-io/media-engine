"""Which windows are the same voice: Ward clustering, in numpy.

Single, average and complete linkage all chain on real speech: one cluster
swallows the room a window at a time. Ward joins the two groups whose union
is tightest, which keeps a voice together and voices apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Halves of a group whose centres are less alike than this are two voices.
#: Two people measured as alike as 0.47 on a good microphone and 0.64 on a
#: poor one, where the room is in every voice. Set lower, people on poor
#: microphones were merged. Set here nobody was, on five recordings, and the
#: price is that someone who speaks at length is now and then found twice.
ALIKE = 0.7
#: The fewest windows a group needs to be asked that question, or to be a voice.
LEAST = 24


@dataclass(frozen=True)
class Join:
    """Two groups made one. Groups 0..n-1 are the points; join `i` makes group n+i."""

    left: int
    right: int
    #: How much looser the union is than its parts. Later joins cost more.
    cost: float
    size: int


def ward(points: Any) -> list[Join]:
    """Every join that takes `points` (rows) down to one group, cheapest first."""
    import numpy as np

    points = np.asarray(points, dtype=np.float64)
    count = len(points)
    if count < 2:
        return []
    # Each group is kept as its centre and its size, which is all Ward needs:
    # no table of every distance, so a day of speech fits in memory.
    centre = points.copy()
    size = np.ones(count)
    square = (centre ** 2).sum(axis=1)
    live = np.ones(count, dtype=bool)
    name = list(range(count))
    found: list[tuple[float, int, int, int]] = []
    chain: list[int] = []
    for _ in range(count - 1):
        if not chain:
            chain.append(int(np.flatnonzero(live)[0]))
        while True:
            here = chain[-1]
            apart = np.maximum(square + square[here] - 2 * (centre @ centre[here]), 0.0)
            cost = np.where(live, 2 * size * size[here] / (size + size[here]) * apart, np.inf)
            cost[here] = np.inf
            nearest = int(cost.argmin())
            # Going back the way it came, when that is as near as anything,
            # is what ends the walk.
            if len(chain) > 1 and cost[chain[-2]] <= cost[nearest]:
                nearest = chain[-2]
                break
            chain.append(nearest)
        chain = chain[:-2]
        total = size[here] + size[nearest]
        found.append((float(np.sqrt(cost[nearest])), name[here], name[nearest], int(total)))
        centre[here] = (size[here] * centre[here] + size[nearest] * centre[nearest]) / total
        square[here] = centre[here] @ centre[here]
        size[here] = total
        live[nearest] = False
    # The walk finds joins out of order. Sorted by cost they are the tree, and
    # each then takes the next free number.
    order = sorted(range(len(found)), key=lambda index: found[index][0])
    return _numbered(found, order, count)


def _numbered(found: list[tuple[float, int, int, int]], order: list[int], count: int) -> list[Join]:
    """The joins in cost order, each side named by the group it now is."""
    parent = list(range(count))

    def root(point: int) -> int:
        while parent[point] != point:
            parent[point] = parent[parent[point]]
            point = parent[point]
        return point

    group = list(range(count))      # root point -> the group's current number
    joins = []
    for position, index in enumerate(order):
        cost, left, right, size = found[index]
        left, right = root(left), root(right)
        joins.append(Join(*sorted((group[left], group[right])), cost, size))
        parent[right] = left
        group[left] = count + position
    return joins


def cut(joins: list[Join], count: int, groups: int) -> list[int]:
    """Which of `groups` groups each of `count` points is in, biggest group first."""
    groups = max(1, min(groups, count))
    member: dict[int, list[int]] = {point: [point] for point in range(count)}
    for position, join in enumerate(joins[:count - groups]):
        member[count + position] = member.pop(join.left) + member.pop(join.right)
    labels = [0] * count
    for label, points in enumerate(sorted(member.values(), key=lambda points: (-len(points), points[0]))):
        for point in points:
            labels[point] = label
    return labels


def voices(points: Any, joins: list[Join] | None = None, *, alike: float = ALIKE, least: int = LEAST) -> list[int]:
    """Which voice each point is, when nobody has said how many there are.

    The tree is read from the top. A group is two voices when its two halves
    point different ways: the cosine between their centres is under `alike`.
    Halves of one voice agree closely once each holds a fair number of
    windows (0.96 and up, measured) and two voices do not (0.64 at most), so
    the question is only asked of halves with at least `least` points. A group too small to be judged is
    not a voice of its own; it goes to the voice it is most like.

    When unsure this splits: two groups given one name are one person again,
    and one group holding two people cannot be put right by naming.
    """
    import numpy as np

    points = np.asarray(points, dtype=np.float64)
    count = len(points)
    if count < 2:
        return [0] * count
    joins = ward(points) if joins is None else joins
    total: dict[int, Any] = {}
    size: dict[int, int] = {}

    def summed(group: int) -> tuple[Any, int]:
        return (points[group], 1) if group < count else (total.pop(group), size.pop(group))

    # A join is undone when its halves are unlike, and so is every join above it.
    undone = [False] * len(joins)
    for position, join in enumerate(joins):
        (left, left_size), (right, right_size) = summed(join.left), summed(join.right)
        unlike = min(left_size, right_size) >= least and _cosine(left, right) < alike
        below = any(undone[side - count] for side in (join.left, join.right) if side >= count)
        undone[position] = unlike or below
        total[count + position], size[count + position] = left + right, left_size + right_size

    member: dict[int, list[int]] = {point: [point] for point in range(count)}
    for position, join in enumerate(joins):
        if not undone[position]:
            member[count + position] = member.pop(join.left) + member.pop(join.right)
    groups = sorted(member.values(), key=lambda group: (-len(group), group[0]))
    kept = [group for group in groups if len(group) >= least] or groups[:1]
    centres = np.stack([points[group].sum(axis=0) for group in kept])
    centres /= np.maximum(np.linalg.norm(centres, axis=1, keepdims=True), 1e-12)
    labels = [0] * count
    for label, group in enumerate(kept):
        for point in group:
            labels[point] = label
    for group in groups:
        if len(group) < least and group is not kept[0]:
            nearest = int((centres @ points[group].sum(axis=0)).argmax())
            for point in group:
                labels[point] = nearest
    return _by_size(labels)


def _cosine(one: Any, other: Any) -> float:
    import numpy as np

    return float(one @ other / max(np.linalg.norm(one) * np.linalg.norm(other), 1e-12))


def _by_size(labels: list[int]) -> list[int]:
    """The same groups, numbered from the biggest down."""
    seen: dict[int, list[int]] = {}
    for point, label in enumerate(labels):
        seen.setdefault(label, []).append(point)
    order = sorted(seen.values(), key=lambda group: (-len(group), group[0]))
    renamed = [0] * len(labels)
    for label, group in enumerate(order):
        for point in group:
            renamed[point] = label
    return renamed
