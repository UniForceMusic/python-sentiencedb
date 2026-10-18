"""Result of a ``sqlite3`` statement."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from .result_abc import ResultABC

R = TypeVar("R")

_STORAGE_CLASSES: Mapping[type, str] = {
    type(None): "NULL",
    int: "INTEGER",
    float: "FLOAT",
    str: "TEXT",
    bytes: "BLOB",
}


class SQLite3Result(ResultABC):
    """Rows of a ``sqlite3`` cursor, read lazily.

    SQLite types values, not columns: :meth:`columns` reports the storage class (``NULL``, ``INTEGER``, ``FLOAT``,
    ``TEXT`` or ``BLOB``) of each value in the first row, and ``None`` when there are no rows.
    """

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor
        self._names = [column[0] for column in cursor.description or ()]
        self._first_row: tuple[Any, ...] | None = None
        self._peeked: list[tuple[Any, ...]] = []

    def columns(self) -> dict[str, str | None]:
        if self._first_row is None and (row := self._fetchone()) is not None:
            self._peeked.append(row)

        if self._first_row is None:
            return dict.fromkeys(self._names)

        return {name: _STORAGE_CLASSES[type(value)] for name, value in zip(self._names, self._first_row, strict=True)}

    def fetch_dict(self) -> dict[str, Any] | None:
        row = self._peeked.pop() if self._peeked else self._fetchone()

        return None if row is None else dict(zip(self._names, row, strict=True))

    def fetch_dicts(self) -> list[dict[str, Any]]:
        rows = [*self._peeked, *self._fetchall()]
        self._peeked.clear()

        return [dict(zip(self._names, row, strict=True)) for row in rows]

    def _fetchone(self) -> tuple[Any, ...] | None:
        if not self._names:
            return None

        row: tuple[Any, ...] | None = _fetch(self._cursor.fetchone)
        self._remember(row)

        return row

    def _fetchall(self) -> list[tuple[Any, ...]]:
        if not self._names:
            return []

        rows: list[tuple[Any, ...]] = _fetch(self._cursor.fetchall)
        self._remember(rows[0] if rows else None)

        return rows

    def _remember(self, row: tuple[Any, ...] | None) -> None:
        if self._first_row is None:
            self._first_row = row


def _fetch(fetch: Callable[[], R]) -> R:
    """Call ``fetch`` with driver errors mapped: SQLite computes rows while they are fetched."""
    from ..adapters._errors import map_sqlite3_error

    try:
        return fetch()
    except sqlite3.Error as error:
        raise map_sqlite3_error(error, None) from error


__all__ = ["SQLite3Result"]
