"""One model on the graphics processor at a time, on the whole machine.

Two models loaded onto one graphics processor at once can stop a machine
dead: each takes the memory it wants, and what is left is the swap file. So
whatever runs a model there holds one lock while it does, and every `media`
on the machine shares it. A second waits for the first; nothing is refused.

Whatever holds the lock may ask for it again: a thread that has it goes
straight in, and lets go only when the first to ask does.
"""

from __future__ import annotations

import contextlib
import fcntl
import threading
from collections.abc import Iterator

from .settings import Settings

#: The lock's file, under the engine's home. Named for the first thing that held it.
LOCK = "mlx.lock"

#: The locks this thread holds, and how many times over.
_mine = threading.local()


@contextlib.contextmanager
def alone(settings: Settings | None = None) -> Iterator[None]:
    """Hold the lock that keeps two models off the graphics processor at once."""
    settings = settings or Settings.from_env()
    lock = settings.home / "locks" / LOCK
    held_here: dict[str, int] = _mine.__dict__.setdefault("held", {})
    if held_here.get(str(lock)):
        held_here[str(lock)] += 1
        try:
            yield
        finally:
            held_here[str(lock)] -= 1
        return
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        held_here[str(lock)] = 1
        try:
            yield
        finally:
            held_here.pop(str(lock), None)
            fcntl.flock(held, fcntl.LOCK_UN)
