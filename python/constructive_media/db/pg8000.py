"""The default database driver: pg8000, a pure-Python PostgreSQL client."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from ..settings import Database
from . import DatabaseError


class Pg8000Driver:
    name = "pg8000"

    def connect(self, database: Database, *, autocommit: bool = True) -> Connection:
        from pg8000 import dbapi

        try:
            raw = dbapi.connect(
                user=database.user,
                password=database.password,
                host=database.host,
                port=database.port,
                database=database.name,
            )
        except Exception as error:  # noqa: BLE001 - the driver raises many kinds
            raise DatabaseError(
                f"could not connect to database {database.name!r} "
                f"on {database.host}:{database.port}: {_message(error)}",
                _sqlstate(error),
            ) from None
        raw.autocommit = autocommit
        _register_numpy(raw)
        return Connection(raw)

    def jsonb(self, value: Any) -> str:
        return json.dumps(value)


class Connection:
    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def cursor(self) -> Cursor:
        return Cursor(self.raw.cursor())

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class Cursor:
    """A DB-API cursor that returns tuples and raises `DatabaseError`."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        try:
            self.raw.execute(sql, tuple(params))
        except Exception as error:  # noqa: BLE001
            raise DatabaseError(_message(error), _sqlstate(error)) from None
        return self

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> Cursor:
        try:
            self.raw.executemany(sql, [tuple(row) for row in rows])
        except Exception as error:  # noqa: BLE001
            raise DatabaseError(_message(error), _sqlstate(error)) from None
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        row = self.raw.fetchone()
        return None if row is None else tuple(row)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in self.raw.fetchall()]

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        return self.raw.rowcount

    @property
    def description(self) -> Any:
        return self.raw.description

    def close(self) -> None:
        self.raw.close()


def _message(error: Exception) -> str:
    detail = error.args[0] if error.args else None
    if isinstance(detail, dict):
        return detail.get("M") or "the database reported an error"
    return str(error) or type(error).__name__


def _sqlstate(error: Exception) -> str | None:
    detail = error.args[0] if error.args else None
    return detail.get("C") if isinstance(detail, dict) else None


def _register_numpy(raw: Any) -> None:
    """Send numpy scalars as plain numbers.

    Analysis code hands back numpy types everywhere. They are registered here,
    once, rather than remembered at each call site.
    """
    try:
        import numpy as np
    except ImportError:
        return
    for kind in (np.float64, np.float32, np.float16):
        raw.register_out_adapter(kind, lambda value: repr(float(value)))
    for kind in (np.int64, np.int32, np.int16, np.int8, np.uint8, np.uint16, np.uint32):
        raw.register_out_adapter(kind, lambda value: repr(int(value)))
    raw.register_out_adapter(np.bool_, lambda value: "true" if value else "false")
