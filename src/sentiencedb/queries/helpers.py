"""Factory functions for value objects (PHP's static ``Query::raw()``, ``Query::alias()``, ...) and escape helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from .objects.alias import Alias, SubQuery
from .objects.sql import CurrentTimestamp, Expression, ExpressionF, Identifier, Raw

if TYPE_CHECKING:
    from .objects.sql import Sql
    from .query import Query
    from .type_aliases import IdentifierLike, Scalar, Value


def alias(identifier: IdentifierLike, alias: str) -> Alias:
    """``<identifier> AS <alias>``."""
    return Alias(identifier, alias)


def current_timestamp(precision: int | None = None) -> CurrentTimestamp:
    """``CURRENT_TIMESTAMP[(precision)]``."""
    return CurrentTimestamp(precision)


def expression(sql: str, params: Sequence[Scalar] | Mapping[str, Scalar] = ()) -> Expression:
    """SQL with ``?`` placeholders plus the values to bind, or ``:name`` placeholders and a mapping."""
    return Expression(sql, params)


def expressionf(format: str, *values: Value) -> ExpressionF:
    """printf style SQL; see :class:`ExpressionF`."""
    return ExpressionF(format, values)


def identifier(identifier: str | Sequence[str | Sql]) -> Identifier:
    """An escaped (optionally qualified) identifier."""
    return Identifier(identifier)


def raw(sql: str) -> Raw:
    """Verbatim SQL."""
    return Raw(sql)


def sub_query(select_query: Query, alias: str) -> SubQuery:
    """``(<select>) AS <alias>``."""
    return SubQuery(select_query, alias)


def column_names(columns: str | Sequence[str]) -> tuple[str, ...]:
    """One column name or several, as a tuple (a ``str`` is never split into characters)."""
    return (columns,) if isinstance(columns, str) else tuple(columns)


def now() -> datetime:
    """The current local date and time (PHP ``new DateTime()``)."""
    return datetime.now()


def escape_ansi(string: str, chars: Iterable[str]) -> str:
    """Escape each of ``chars`` by doubling it (``'`` becomes ``''``)."""
    for char in dict.fromkeys(chars):
        string = string.replace(char, char * 2)

    return string


def escape_backslash(string: str, chars: Iterable[str]) -> str:
    """Escape backslashes and each of ``chars`` by prefixing a backslash (``%`` becomes ``\\%``)."""
    for char in dict.fromkeys(("\\", *chars)):
        string = string.replace(char, "\\" + char)

    return string


__all__ = [
    "alias",
    "current_timestamp",
    "escape_ansi",
    "escape_backslash",
    "expression",
    "expressionf",
    "identifier",
    "now",
    "raw",
    "sub_query",
]
