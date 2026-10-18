"""Inline SQL fragments: the :class:`Sql` interface and its implementations."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ...exceptions import QueryError
from ..query import Query
from .query_with_params import QueryWithParams

if TYPE_CHECKING:
    from ...dialects.dialect_abc import DialectABC
    from ..type_aliases import Scalar, Value


class Sql(ABC):
    """A SQL fragment that is placed into a statement as is.

    Wherever a value is expected, the dialect inlines :meth:`sql` and binds :meth:`params`. Wherever an identifier is
    expected (columns, tables, ``ORDER BY``, defaults, ...), the dialect inlines :meth:`raw_sql` instead.
    """

    @abstractmethod
    def sql(self, dialect: DialectABC) -> str:
        """The fragment with ``?`` placeholders."""

    @abstractmethod
    def params(self, dialect: DialectABC) -> list[Scalar]:
        """The parameters for the placeholders in :meth:`sql`."""

    @abstractmethod
    def raw_sql(self, dialect: DialectABC) -> str:
        """The fragment with every parameter interpolated as a literal."""


@dataclass(frozen=True, slots=True)
class Raw(Sql):
    """Verbatim SQL without parameters, e.g. ``raw("count(*)")``."""

    text: str

    def sql(self, dialect: DialectABC) -> str:
        return self.text

    def params(self, dialect: DialectABC) -> list[Scalar]:
        return []

    def raw_sql(self, dialect: DialectABC) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class Identifier(Sql):
    """An escaped identifier, e.g. ``identifier(["users", "id"])`` renders as ``"users"."id"``."""

    identifier: str | Sequence[str | Sql]

    def sql(self, dialect: DialectABC) -> str:
        return dialect.escape_identifier(self.identifier)

    def params(self, dialect: DialectABC) -> list[Scalar]:
        return []

    def raw_sql(self, dialect: DialectABC) -> str:
        return self.sql(dialect)


@dataclass(frozen=True, slots=True)
class Expression(Sql):
    """SQL with ``?`` placeholders plus the values to bind, e.g. ``expression("coalesce(a, ?)", [0])``."""

    text: str
    values: Sequence[Scalar] = ()

    def sql(self, dialect: DialectABC) -> str:
        return self.text

    def params(self, dialect: DialectABC) -> list[Scalar]:
        return list(self.values)

    def raw_sql(self, dialect: DialectABC) -> str:
        return QueryWithParams(self.text, self.values).to_sql(dialect)


@dataclass(frozen=True, slots=True)
class CurrentTimestamp(Sql):
    """``CURRENT_TIMESTAMP``, or ``CURRENT_TIMESTAMP(n)`` when a non-zero ``precision`` is given."""

    precision: int | None = None

    def sql(self, dialect: DialectABC) -> str:
        if self.precision:
            return f"CURRENT_TIMESTAMP({abs(self.precision)})"

        return "CURRENT_TIMESTAMP"

    def params(self, dialect: DialectABC) -> list[Scalar]:
        return []

    def raw_sql(self, dialect: DialectABC) -> str:
        return self.sql(dialect)


_BOOL_MODIFIERS = "b"
_INT_MODIFIERS = "cdouxX"
_FLOAT_MODIFIERS = "eEfFgGhH"
_STRING_MODIFIERS = "s"
_FORMAT_PATTERN = re.compile(
    rf"%%|%(?P<precision>\.[0-9]{{0,53}})?(?P<modifier>[{_BOOL_MODIFIERS}{_INT_MODIFIERS}{_FLOAT_MODIFIERS}{_STRING_MODIFIERS}])"
)


@dataclass(frozen=True, slots=True)
class ExpressionF(Sql):
    """printf style SQL: ``expressionf("price > %f AND name = %s", 9.95, "x")``.

    Each ``%<modifier>`` consumes the next value. Plain values become bound parameters, cast according to the
    modifier: ``b`` bool; ``c d o u x X`` int; ``e E f F g G h H`` float (``%.2f`` rounds to 2 decimals); ``s`` str.
    ``None`` binds NULL and dates are bound unchanged. A :class:`Sql` value is inlined, and a :class:`Query` value is
    inlined as its SQL (without parentheses), both with their params merged. ``%%`` renders a literal ``%``.

    :raises QueryError: (on render) Fewer values than ``%`` modifiers.
    """

    format: str
    values: Sequence[Value] = ()

    def sql(self, dialect: DialectABC) -> str:
        return self._build(dialect)[0]

    def params(self, dialect: DialectABC) -> list[Scalar]:
        return self._build(dialect)[1]

    def raw_sql(self, dialect: DialectABC) -> str:
        return self._build(dialect)[2]

    def _build(self, dialect: DialectABC) -> tuple[str, list[Scalar], str]:
        sql: list[str] = []
        raw_sql: list[str] = []
        params: list[Scalar] = []
        position = 0
        index = 0

        for match in _FORMAT_PATTERN.finditer(self.format):
            literal = self.format[position : match.start()]
            sql.append(literal)
            raw_sql.append(literal)
            position = match.end()

            modifier = match["modifier"]

            if modifier is None:
                sql.append("%")
                raw_sql.append("%")

                continue

            if index >= len(self.values):
                raise QueryError(f"{index + 1} arguments are required, {len(self.values)} given")

            value = self.values[index]
            index += 1

            if isinstance(value, Query):
                query_with_params = value.to_query_with_params()
                sql.append(query_with_params.query)
                raw_sql.append(query_with_params.to_sql(dialect))
                params.extend(query_with_params.params)
            elif isinstance(value, Sql):
                sql.append(value.sql(dialect))
                raw_sql.append(value.raw_sql(dialect))
                params.extend(value.params(dialect))
            else:
                param = _cast(value, modifier, match["precision"])
                sql.append("?")
                raw_sql.append(dialect.cast_to_query(param))
                params.append(param)

        literal = self.format[position:]
        sql.append(literal)
        raw_sql.append(literal)

        return "".join(sql), params, "".join(raw_sql)


def _cast(value: Scalar, modifier: str, precision: str | None) -> Scalar:
    if value is None or isinstance(value, date):
        return value

    if modifier in _BOOL_MODIFIERS:
        return bool(value)

    if isinstance(value, bytes):
        return value

    if modifier in _INT_MODIFIERS:
        return int(value)

    if modifier in _FLOAT_MODIFIERS:
        number = float(value)

        return round(number, int(precision[1:] or 0)) if precision else number

    return str(value)


__all__ = ["CurrentTimestamp", "Expression", "ExpressionF", "Identifier", "Raw", "Sql"]
