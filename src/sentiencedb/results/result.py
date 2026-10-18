"""In-memory result set."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Self

from .result_abc import ResultABC


class Result(ResultABC):
    """A result held in memory: a snapshot of another result, or rows built by the library (emulated statements).

    :param columns: Column name to native type name.
    :param rows: The rows; each is copied.
    """

    def __init__(self, columns: Mapping[str, str | None] | None = None, rows: Sequence[Mapping[str, Any]] = ()) -> None:
        self._columns = dict(columns or {})
        self._rows = [dict(row) for row in rows]
        self._position = 0

    @classmethod
    def from_result(cls, result: ResultABC) -> Self:
        """Drain ``result`` into memory, e.g. to read it later from another thread. (PHP ``fromInterface``.)"""
        return cls(result.columns(), result.fetch_dicts())

    def columns(self) -> dict[str, str | None]:
        return dict(self._columns)

    def fetch_dict(self) -> dict[str, Any] | None:
        if self._position >= len(self._rows):
            return None

        row = self._rows[self._position]
        self._position += 1

        return row

    def fetch_dicts(self) -> list[dict[str, Any]]:
        rows = self._rows[self._position :]
        self._position = len(self._rows)

        return rows

    def __len__(self) -> int:
        """Number of rows not fetched yet."""
        return len(self._rows) - self._position

    def __repr__(self) -> str:
        return f"Result(columns={self._columns!r}, remaining_rows={len(self)})"


__all__ = ["Result"]
