"""One model on the graphics processor at a time, on the whole machine.

Two models loaded onto one graphics processor at once can stop a machine
dead: each takes the memory it wants, and what is left is the swap file. So
whatever runs a model there holds one lock while it does, and every `media`
on the machine shares it. A second waits for the first; nothing is refused.
"""

from __future__ import annotations

import contextlib
import fcntl
from collections.abc import Iterator

from .settings import Settings

#: The lock's file, under the engine's home. Named for the first thing that held it.
LOCK = "mlx.lock"


@contextlib.contextmanager
def alone(settings: Settings | None = None) -> Iterator[None]:
    """Hold the lock that keeps two models off the graphics processor at once."""
    settings = settings or Settings.from_env()
    lock = settings.home / "locks" / LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
