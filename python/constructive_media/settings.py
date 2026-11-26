"""Settings: every local choice in one place, read from the environment.

Nothing else in the engine reads an environment variable, expands a home
directory or names a database. A module that needs to know where something
lives, or which adapter to use, asks for a `Settings`.

| Variable                                | Meaning                                  | Default                     |
| --------------------------------------- | ---------------------------------------- | --------------------------- |
| `CONSTRUCTIVE_MEDIA_HOME`               | Root for everything the engine writes    | `~/.constructive-media`     |
| `CONSTRUCTIVE_MEDIA_WORK_DIR`           | Derived files; safe to delete            | `<home>/work`               |
| `CONSTRUCTIVE_MEDIA_MODELS_DIR`         | Models you downloaded                    | `<home>/models`             |
| `CONSTRUCTIVE_MEDIA_DATABASE_URL`       | PostgreSQL connection string             | unset: the `PG*` variables  |
| `CONSTRUCTIVE_MEDIA_ADAPTER_<SLOT>`     | Adapter to use for a slot                | the slot's default          |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

ENV_PREFIX = "CONSTRUCTIVE_MEDIA_"
ADAPTER_PREFIX = ENV_PREFIX + "ADAPTER_"
DEFAULT_HOME = "~/.constructive-media"


@dataclass(frozen=True)
class Settings:
    """Where things live and which adapters are chosen."""

    home: Path
    work_dir: Path
    models_dir: Path
    database_url: str | None = None
    adapters: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Read settings from `env`, or from the process environment."""
        env = os.environ if env is None else env

        def value(name: str) -> str | None:
            return env.get(ENV_PREFIX + name) or None

        def path(name: str, default: Path) -> Path:
            raw = value(name)
            return Path(raw).expanduser() if raw else default

        home = Path(value("HOME") or DEFAULT_HOME).expanduser()
        adapters = {
            key[len(ADAPTER_PREFIX):].lower(): chosen
            for key, chosen in env.items()
            if key.startswith(ADAPTER_PREFIX) and chosen
        }
        return cls(
            home=home,
            work_dir=path("WORK_DIR", home / "work"),
            models_dir=path("MODELS_DIR", home / "models"),
            database_url=value("DATABASE_URL"),
            adapters=MappingProxyType(adapters),
        )

    def adapter(self, slot: str) -> str | None:
        """The adapter chosen for `slot`, or None to use the slot's default."""
        return self.adapters.get(slot)
