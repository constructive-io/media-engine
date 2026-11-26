"""Database access for every part of the engine.

Thin on purpose. The schema is the interesting artefact, and an ORM would hide
it behind an abstraction that reads worse than the SQL it replaces.

    cur = db.cursor()
    cur.execute("SELECT id, path FROM media.assets WHERE project = %s", (project,))
    for asset_id, path in cur.fetchall():
        ...

Parameters are written `%s`. Rows come back as tuples. A failed statement
raises `DatabaseError`, whatever the driver underneath.

The driver itself is an adapter in the `database` slot, so no module but the
driver's own names a PostgreSQL library.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, runtime_checkable

from .. import adapters
from ..settings import Database, Settings


class DatabaseError(Exception):
    """A statement failed or the database could not be reached."""

    def __init__(self, message: str, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


@runtime_checkable
class Driver(Protocol):
    """What a database adapter provides."""

    name: str

    def connect(self, database: Database, *, autocommit: bool = True) -> Any:
        """A connection whose cursors take `%s` parameters and return tuples.

        The connection has `cursor()`, `commit()`, `rollback()` and `close()`.
        Failures are raised as `DatabaseError`.
        """
        ...

    def jsonb(self, value: Any) -> Any:
        """Wrap a Python value so it is sent as one JSON document."""
        ...


_shared: Any = None
_current: ContextVar[Any] = ContextVar("constructive_media_connection", default=None)


def driver(settings: Settings | None = None) -> Driver:
    """The configured database driver."""
    return adapters.get("database", settings=settings)


def connect(settings: Settings | None = None, *, autocommit: bool = True) -> Any:
    """Open a new connection. The caller closes it."""
    settings = settings or Settings.from_env()
    return driver(settings).connect(settings.database, autocommit=autocommit)


def connection() -> Any:
    """The connection in use: the one given to `use`, else a shared one."""
    global _shared
    current = _current.get()
    if current is not None:
        return current
    if _shared is None:
        _shared = connect()
    return _shared


def cursor() -> Any:
    """A cursor on the connection in use. Statements commit as they run."""
    return connection().cursor()


@contextmanager
def use(conn: Any) -> Iterator[Any]:
    """Run everything inside the block on `conn`.

    This is how a caller wraps several engine calls in one transaction, and
    how tests keep their rows out of the database.
    """
    token = _current.set(conn)
    try:
        yield conn
    finally:
        _current.reset(token)


@contextmanager
def transaction(*, keep: bool = True) -> Iterator[Any]:
    """Run everything inside the block as one unit: all of it stored, or none.

    With `keep=False` none of it is stored even when it succeeds, which is how
    a command shows what it would do. Inside `use`, the block is a savepoint
    in that connection's transaction.
    """
    outer = _current.get()
    if outer is not None:
        cur = outer.cursor()
        cur.execute("SAVEPOINT constructive_media_unit")
        try:
            yield outer
        except BaseException:
            cur.execute("ROLLBACK TO SAVEPOINT constructive_media_unit")
            raise
        cur.execute(("RELEASE" if keep else "ROLLBACK TO") + " SAVEPOINT constructive_media_unit")
        return
    conn = connect(autocommit=False)
    try:
        with use(conn):
            yield conn
        if keep:
            conn.commit()
        else:
            conn.rollback()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def jsonb(value: Any) -> Any:
    """A parameter sent as one JSON document, whatever its Python type."""
    return driver().jsonb(value)


def close() -> None:
    """Close the shared connection, if one was opened."""
    global _shared
    if _shared is not None:
        _shared.close()
        _shared = None
