"""Result of a psycopg statement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .result_abc import ResultABC

if TYPE_CHECKING:
    from psycopg import Cursor


class PsycopgResult(ResultABC):
    """Rows of a psycopg cursor (psycopg reads them into memory when the statement executes).

    :meth:`columns` reports PostgreSQL type names in upper case (``INT4``, ``TEXT``, ``TIMESTAMPTZ``, ``INT4[]`` for
    arrays), ``None`` for types psycopg does not know (e.g. user defined enums).
    """

    def __init__(self, cursor: Cursor[tuple[Any, ...]]) -> None:
        self._cursor = cursor
        self._names = [column.name for column in cursor.description or ()]

    def columns(self) -> dict[str, str | None]:
        types = self._cursor.adapters.types
        columns: dict[str, str | None] = {}

        for column in self._cursor.description or ():
            type_info = types.get(column.type_code)

            if type_info is None:
                columns[column.name] = None
            elif column.type_code == type_info.array_oid:
                # The registry finds an element type by its array oid too.
                columns[column.name] = f"{type_info.name.upper()}[]"
            else:
                columns[column.name] = type_info.name.upper()

        return columns

    def fetch_dict(self) -> dict[str, Any] | None:
        if not self._names:
            return None

        row = self._cursor.fetchone()

        return None if row is None else dict(zip(self._names, row, strict=True))

    def fetch_dicts(self) -> list[dict[str, Any]]:
        if not self._names:
            return []

        return [dict(zip(self._names, row, strict=True)) for row in self._cursor.fetchall()]


__all__ = ["PsycopgResult"]
