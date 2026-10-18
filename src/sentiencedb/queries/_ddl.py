"""Helpers shared by the DDL builders."""

from __future__ import annotations

from collections.abc import Sequence


def column_names(columns: str | Sequence[str]) -> tuple[str, ...]:
    """One column name or several, as a tuple."""
    return (columns,) if isinstance(columns, str) else tuple(columns)
