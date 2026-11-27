"""Settings: every local choice in one place, read from the environment.

Nothing else in the engine reads an environment variable, expands a home
directory or names a database. A module that needs to know where something
lives, or which adapter to use, asks for a `Settings`.

| Variable                              | Meaning                                | Default                    |
| ------------------------------------- | -------------------------------------- | -------------------------- |
| `CONSTRUCTIVE_MEDIA_HOME`             | Root for everything the engine writes  | `~/.constructive-media`    |
| `CONSTRUCTIVE_MEDIA_WORK_DIR`         | Derived files; safe to delete          | `<home>/work`              |
| `CONSTRUCTIVE_MEDIA_MODELS_DIR`       | Models you downloaded                  | `<home>/models`            |
| `CONSTRUCTIVE_MEDIA_DATABASE_URL`     | `postgres://user:pass@host:port/name`  | the `PG*` variables        |
| `CONSTRUCTIVE_MEDIA_DEVICE`           | `auto`, `cpu`, `mps` or `cuda`         | `auto`                     |
| `CONSTRUCTIVE_MEDIA_ADAPTER_<SLOT>`   | Adapter to use for a slot              | the slot's default         |
| `CONSTRUCTIVE_MEDIA_MODEL_<SLOT>`     | Model that slot's adapter should load  | the adapter's default      |
| `CONSTRUCTIVE_MEDIA_URL_<SLOT>`       | Server that slot's adapter talks to    | none                       |
| `CONSTRUCTIVE_MEDIA_KEY_<SLOT>`       | Key to send that server, if it asks    | none                       |
| `CONSTRUCTIVE_MEDIA_EXTRA_<SLOT>`     | More to send it with each request, as a JSON object | none          |
| `CONSTRUCTIVE_MEDIA_LIBRARY_INDEX`    | Where libraries are found by name: folders or git addresses, with commas between; `none` for nowhere | `constructive-io/media-engine-libraries` |
| `CONSTRUCTIVE_MEDIA_BOILERPLATES`     | Where `media init` finds what a project starts from: a folder or a git address | `constructive-io/media-engine-boilerplates` |

Without a database URL the standard `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`
and `PGDATABASE` are used, and the database is called `media`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

ENV_PREFIX = "CONSTRUCTIVE_MEDIA_"
ADAPTER_PREFIX = ENV_PREFIX + "ADAPTER_"
MODEL_PREFIX = ENV_PREFIX + "MODEL_"
URL_PREFIX = ENV_PREFIX + "URL_"
KEY_PREFIX = ENV_PREFIX + "KEY_"
EXTRA_PREFIX = ENV_PREFIX + "EXTRA_"
DEFAULT_HOME = "~/.constructive-media"
DEFAULT_DATABASE = "media"
#: The index a library is looked for in by name, unless the environment says otherwise.
DEFAULT_INDEX = "constructive-io/media-engine-libraries"
#: Where `media init` finds its templates when it is not told.
DEFAULT_BOILERPLATES = "constructive-io/media-engine-boilerplates"


class SettingsError(ValueError):
    """A setting has a value that cannot be used."""


@dataclass(frozen=True)
class Database:
    """Where the engine's PostgreSQL database is."""

    name: str = DEFAULT_DATABASE
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str | None = field(default=None, repr=False)

    @classmethod
    def from_url(cls, url: str) -> Database:
        parts = urlsplit(url)
        if parts.scheme not in ("postgres", "postgresql"):
            raise SettingsError("the database URL must start with postgres:// or postgresql://")
        try:
            port = parts.port or 5432
        except ValueError:
            raise SettingsError("the database URL has a port that is not a number") from None
        return cls(
            name=unquote(parts.path.lstrip("/")) or DEFAULT_DATABASE,
            host=parts.hostname or "localhost",
            port=port,
            user=unquote(parts.username) if parts.username else "postgres",
            password=unquote(parts.password) if parts.password else None,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Database:
        port = env.get("PGPORT") or "5432"
        if not port.isdigit():
            raise SettingsError(f"PGPORT must be a number, got {port!r}")
        return cls(
            name=env.get("PGDATABASE") or DEFAULT_DATABASE,
            host=env.get("PGHOST") or "localhost",
            port=int(port),
            user=env.get("PGUSER") or "postgres",
            password=env.get("PGPASSWORD") or None,
        )


def _frozen() -> Mapping[str, str]:
    return MappingProxyType({})


@dataclass(frozen=True)
class Settings:
    """Where things live and which adapters and models are chosen."""

    home: Path
    work_dir: Path
    models_dir: Path
    database: Database = field(default_factory=Database)
    device: str = "auto"
    adapters: Mapping[str, str] = field(default_factory=_frozen)
    models: Mapping[str, str] = field(default_factory=_frozen)
    urls: Mapping[str, str] = field(default_factory=_frozen)
    #: Never shown: a key is a secret.
    keys: Mapping[str, str] = field(default_factory=_frozen, repr=False)
    extras: Mapping[str, str] = field(default_factory=_frozen)
    #: The indexes libraries are found in by name, the first to name one winning.
    indexes: tuple[str, ...] = ()
    #: Where `media init` finds what a project starts from.
    boilerplates: str = DEFAULT_BOILERPLATES

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Read settings from `env`, or from the process environment."""
        env = os.environ if env is None else env

        def value(name: str) -> str | None:
            return env.get(ENV_PREFIX + name) or None

        def path(name: str, default: Path) -> Path:
            raw = value(name)
            return Path(raw).expanduser() if raw else default

        def per_slot(prefix: str) -> Mapping[str, str]:
            return MappingProxyType({
                key[len(prefix):].lower(): chosen
                for key, chosen in env.items()
                if key.startswith(prefix) and chosen
            })

        home = Path(value("HOME") or DEFAULT_HOME).expanduser()
        index = value("LIBRARY_INDEX") or DEFAULT_INDEX
        indexes = () if index.strip().lower() in ("none", "off") else tuple(
            one.strip() for one in index.split(",") if one.strip())
        url = value("DATABASE_URL")
        return cls(
            home=home,
            work_dir=path("WORK_DIR", home / "work"),
            models_dir=path("MODELS_DIR", home / "models"),
            database=Database.from_url(url) if url else Database.from_env(env),
            device=(value("DEVICE") or "auto").lower(),
            adapters=per_slot(ADAPTER_PREFIX),
            models=per_slot(MODEL_PREFIX),
            urls=per_slot(URL_PREFIX),
            keys=per_slot(KEY_PREFIX),
            extras=per_slot(EXTRA_PREFIX),
            indexes=indexes,
            boilerplates=value("BOILERPLATES") or DEFAULT_BOILERPLATES,
        )

    def adapter(self, slot: str) -> str | None:
        """The adapter chosen for `slot`, or None to use the slot's default."""
        return self.adapters.get(slot)

    def model(self, slot: str) -> str | None:
        """The model chosen for `slot`, or None to use the adapter's default."""
        return self.models.get(slot)

    def url(self, slot: str) -> str | None:
        """The server the adapter for `slot` talks to, for adapters that talk to one."""
        return self.urls.get(slot)

    def key(self, slot: str) -> str | None:
        """The key to send that server, or None when it asks for none."""
        return self.keys.get(slot)

    def extra(self, slot: str) -> str | None:
        """What else that server is to be sent with each request, as it was written."""
        return self.extras.get(slot)
