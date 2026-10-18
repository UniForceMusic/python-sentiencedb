"""Result of a ``sqlite3`` statement."""

from __future__ import annotations

import sqlite3
from collections import deque
from collections.abc import Mapping
from typing import Any

from .result_abc import ResultABC

_STORAGE_CLASSES: Mapping[type, str] = {
    type(None): "NULL",
    int: "INTEGER",
    float: "FLOAT",
    str: "TEXT",
    bytes: "BLOB",
}


class SQLite3Result(ResultABC):
    """Rows of a ``sqlite3`` cursor.

    Every row is read when the result is created, so the statement is finished and holds no lock on the database
    (SQLite computes rows while they are fetched; errors doing so are raised by the statement, not by a fetch).

    SQLite types values, not columns: :meth:`columns` reports the storage class (``NULL``, ``INTEGER``, ``FLOAT``,
    ``TEXT`` or ``BLOB``) of each value in the first row, and ``None`` when there are no rows.
    """

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        names = [column[0] for column in cursor.description or ()]
        rows: list[tuple[Any, ...]] = cursor.fetchall() if names else []

        if rows:
            self._columns: dict[str, str | None] = {
                name: _STORAGE_CLASSES[type(value)] for name, value in zip(names, rows[0], strict=True)
            }
        else:
            self._columns = dict.fromkeys(names)

        self._rows = deque(dict(zip(names, row, strict=True)) for row in rows)

    def columns(self) -> dict[str, str | None]:
        return dict(self._columns)

    def fetch_dict(self) -> dict[str, Any] | None:
        return self._rows.popleft() if self._rows else None

    def fetch_dicts(self) -> list[dict[str, Any]]:
        rows = list(self._rows)
        self._rows.clear()

        return rows


__all__ = ["SQLite3Result"]
