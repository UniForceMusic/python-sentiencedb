"""Result of a mysql-connector statement."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from .result_abc import ResultABC

if TYPE_CHECKING:
    from mysql.connector.abstracts import MySQLCursorAbstract


class MySQLConnectorResult(ResultABC):
    """Rows of a mysql-connector cursor.

    Every row is read when the result is created, so the cursor can be closed and the connection reused right
    away. :meth:`columns` reports mysql-connector field type names (``LONG``, ``VAR_STRING``, ``DATETIME``, ...).
    """

    def __init__(self, cursor: MySQLCursorAbstract) -> None:
        from mysql.connector import FieldType

        description = cursor.description or []
        names = [column[0] for column in description]

        self._columns: dict[str, str | None] = {}

        for column in description:
            type_name = FieldType.get_info(column[1])
            self._columns[column[0]] = type_name if isinstance(type_name, str) else None

        self._rows = deque(dict(zip(names, row, strict=True)) for row in (cursor.fetchall() if names else []))

    def columns(self) -> dict[str, str | None]:
        return dict(self._columns)

    def fetch_dict(self) -> dict[str, Any] | None:
        return self._rows.popleft() if self._rows else None

    def fetch_dicts(self) -> list[dict[str, Any]]:
        rows = list(self._rows)
        self._rows.clear()

        return rows


__all__ = ["MySQLConnectorResult"]
