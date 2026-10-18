"""Generic ANSI-ish dialect; the base of every concrete dialect."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar

from ..exceptions import QueryError
from ..queries.enums import ConditionEnum, JoinEnum, TypeEnum
from ..queries.objects.alias import Alias, SubQuery
from ..queries.objects.clauses import ConditionGroup
from ..queries.objects.ddl import (
    AddColumn,
    AddForeignKeyConstraint,
    AddPrimaryKeys,
    AddUniqueConstraint,
    AlterColumn,
    DropColumn,
    DropConstraint,
    ForeignKeyConstraint,
    RenameColumn,
    UniqueConstraint,
)
from ..queries.objects.query_with_params import QueryWithParams
from ..queries.objects.sql import Identifier, Raw, Sql
from ..queries.query import Query
from .dialect_abc import DialectABC

if TYPE_CHECKING:
    from ..queries.objects.clauses import Condition, ConditionNode, JoinClause, OnConflict, OrderBy, UnionClause
    from ..queries.objects.ddl import Alter, Column, TableConstraint, Type
    from ..queries.type_aliases import ConditionValue, IdentifierLike, Scalar, SelectColumn, TableRef, Value

_TRUE_STRINGS = frozenset({"true", "t", "yes", "y", "on"})

_NEGATED_CONDITIONS = frozenset(
    {
        ConditionEnum.NOT_EQUALS,
        ConditionEnum.NOT_BETWEEN,
        ConditionEnum.NOT_LIKE,
        ConditionEnum.NOT_GLOB,
        ConditionEnum.NOT_IN,
        ConditionEnum.NOT_REGEX,
        ConditionEnum.NOT_EXISTS,
    }
)

_REGEX_FLAGS = frozenset("ims")
"""The portable regex flags: ``i`` ignores case, ``m`` makes ``^``/``$`` match at line breaks and ``s`` lets ``.``
match a newline."""


@dataclass(frozen=True, slots=True)
class _GlobClass:
    """A ``[...]`` GLOB character class: its ``(low, high)`` ranges (``low == high`` for one character)."""

    negate: bool
    ranges: list[tuple[str, str]]


def _join(*parts: str) -> str:
    """Join the non-empty statement parts with single spaces."""
    return " ".join(part for part in parts if part)


def _portable_regex_flags(flags: str) -> str:
    """``flags`` without duplicates.

    :raises QueryError: A flag other than ``i``, ``m`` and ``s``.
    """
    unknown = set(flags) - _REGEX_FLAGS

    if unknown:
        raise QueryError(f"unsupported regex flags {''.join(sorted(unknown))!r}; the portable flags are i, m and s")

    return "".join(dict.fromkeys(flags))


def _split_path(table: IdentifierLike) -> tuple[list[str | Sql], IdentifierLike]:
    """``(schema path, bare table)`` of a table identifier: ``["s", "t"]`` -> ``(["s"], "t")``, ``"t"`` ->
    ``([], "t")``. An :class:`Identifier` is split like its path; other :class:`Sql` cannot be split."""
    path = table.identifier if isinstance(table, Identifier) else table

    if isinstance(path, str | Sql) or len(path) < 2:
        return [], table

    return list(path[:-1]), path[-1]


def _qualified(table: IdentifierLike, name: str) -> IdentifierLike:
    """``name`` qualified with the schema path of ``table``: ``(["s", "t"], "i")`` -> ``["s", "i"]``."""
    schema, _ = _split_path(table)

    return [*schema, name] if schema else name


def _glob_tokens(pattern: str) -> list[str | _GlobClass]:
    """Tokenize a GLOB pattern the way SQLite reads it: the wildcards ``*`` and ``?``, ``[...]`` classes
    (``[^...]`` negated; a leading ``]`` and a ``-`` that cannot form a range are literal) and literal characters.
    Outside a class ``*`` and ``?`` are always wildcards, so every other string token is a literal.

    :raises QueryError: A ``[`` without its closing ``]``.
    """
    tokens: list[str | _GlobClass] = []
    index = 0

    while index < len(pattern):
        char = pattern[index]
        index += 1

        if char != "[":
            tokens.append(char)
            continue

        negate = pattern.startswith("^", index)
        index += negate
        ranges: list[tuple[str, str]] = []

        if pattern.startswith("]", index):
            ranges.append(("]", "]"))
            index += 1

        prior: str | None = None

        while index < len(pattern) and pattern[index] != "]":
            char = pattern[index]

            if char == "-" and prior is not None and index + 1 < len(pattern) and pattern[index + 1] != "]":
                ranges[-1] = (prior, pattern[index + 1])
                prior = None
                index += 2
                continue

            ranges.append((char, char))
            prior = char
            index += 1

        if index >= len(pattern):
            raise QueryError(f"unterminated [ in GLOB pattern {pattern!r}")

        tokens.append(_GlobClass(negate, ranges))
        index += 1

    return tokens


def _glob_to_like(tokens: Sequence[str | _GlobClass]) -> str | None:
    """The ``LIKE`` pattern (backslash escapes) matching the same strings, or ``None`` when a character class needs
    a regex. A class of one character (``[*]``, ``[[]``) is a literal."""
    parts: list[str] = []

    for token in tokens:
        if isinstance(token, _GlobClass):
            match token:
                case _GlobClass(negate=False, ranges=[(low, high)]) if low == high:
                    parts.append(_like_literal(low))
                case _:
                    return None
        elif token == "*":
            parts.append("%")
        elif token == "?":
            parts.append("_")
        else:
            parts.append(_like_literal(token))

    return "".join(parts)


def _like_literal(char: str) -> str:
    return f"\\{char}" if char in "\\%_" else char


def _glob_to_regex(tokens: Sequence[str | _GlobClass]) -> str:
    """An anchored regex matching the same strings (with the ``s`` flag, as ``*`` and ``?`` match newlines). ASCII
    punctuation is backslash escaped, which every engine's regex dialect reads as the literal character."""
    parts = ["^"]

    for token in tokens:
        if isinstance(token, _GlobClass):
            items = "".join(
                _regex_literal(low) if low == high else f"{_regex_literal(low)}-{_regex_literal(high)}"
                for low, high in token.ranges
            )
            parts.append(f"[{'^' if token.negate else ''}{items}]")
        elif token == "*":
            parts.append(".*")
        elif token == "?":
            parts.append(".")
        else:
            parts.append(_regex_literal(token))

    parts.append("$")

    return "".join(parts)


def _regex_literal(char: str) -> str:
    return f"\\{char}" if char.isascii() and not char.isalnum() and not char.isspace() else char


class SQLDialect(DialectABC):
    """Generic SQL generation shared by all dialects.

    Engine differences are expressed through the class constants below, through the feature flag properties
    (version gated in subclasses) and by overriding the protected ``_build_*`` hooks. Hooks return SQL fragments
    and append the parameters of the ``?`` placeholders they emit to ``params``, in placeholder order.
    """

    DATETIME_FORMAT: ClassVar[str] = "%Y-%m-%d %H:%M:%S"
    """``strftime`` format of datetime parameters and literals."""
    ESCAPE_IDENTIFIER: ClassVar[str] = '"'
    ESCAPE_STRING: ClassVar[str] = "'"
    ESCAPE_CHARS: ClassVar[Mapping[str, str]] = {"\0": ""}
    """Extra replacements applied inside string literals (never inside identifiers)."""
    BOOLEAN: ClassVar[bool] = True
    DISTINCT_ON: ClassVar[bool] = False
    DROP_INDEX_ON_TABLE: ClassVar[bool] = False
    GENERATED_BY_DEFAULT_AS_IDENTITY: ClassVar[bool] = True
    INDEX_EXISTS: ClassVar[bool] = False
    LATERAL: ClassVar[bool] = True
    ON_CONFLICT: ClassVar[bool] = False
    RETURNING: ClassVar[bool] = False
    SAVEPOINTS: ClassVar[bool] = True
    TABLE_EXISTS: ClassVar[bool] = False

    # -- statements ---------------------------------------------------------------------------------------------

    def select(
        self,
        *,
        table: TableRef,
        columns: Sequence[SelectColumn] = (),
        distinct: Sequence[IdentifierLike] | None = None,
        joins: Sequence[JoinClause | Sql] = (),
        where: Sequence[ConditionNode] = (),
        group_by: Sequence[IdentifierLike] = (),
        having: Sequence[ConditionNode] = (),
        order_by: Sequence[OrderBy] = (),
        limit: int | None = None,
        offset: int | None = None,
        unions: Sequence[UnionClause] = (),
    ) -> QueryWithParams:
        params: list[Scalar] = []
        query = _join(
            "SELECT",
            self._build_distinct(distinct),
            self._build_columns(params, columns),
            "FROM",
            self._build_reference(params, table),
            self._build_joins(params, joins),
            self._build_clause("WHERE", params, where),
            self._build_group_by(group_by),
            self._build_clause("HAVING", params, having),
            self._build_order_by(order_by),
            self._build_limit(limit, offset),
        )

        if unions:
            query = self._build_unions(params, query, unions)

        return QueryWithParams(query, params)

    def insert(
        self,
        *,
        table: IdentifierLike,
        values: Sequence[Mapping[str, Value]],
        on_conflict: OnConflict | None = None,
        returning: Sequence[str] | None = None,
        last_insert_id: str | None = None,
    ) -> QueryWithParams:
        if not values:
            raise QueryError("no insert values specified")

        params: list[Scalar] = []
        columns = list(dict.fromkeys(column for row in values for column in row))
        rows = ", ".join(self._build_row(params, columns, row) for row in values)
        query = _join(
            "INSERT INTO",
            self.escape_identifier(table),
            f"({self._escape_all(columns)})",
            "VALUES",
            rows,
            self._build_on_conflict(params, on_conflict, columns, last_insert_id),
            self._build_returning(returning) if self.returning else "",
        )

        return QueryWithParams(query, params)

    def update(
        self,
        *,
        table: IdentifierLike,
        updates: Mapping[str, Value],
        where: Sequence[ConditionNode] = (),
        returning: Sequence[str] | None = None,
    ) -> QueryWithParams:
        if not updates:
            raise QueryError("no updates specified")

        params: list[Scalar] = []
        query = _join(
            "UPDATE",
            self.escape_identifier(table),
            "SET",
            self._build_set(params, updates),
            self._build_clause("WHERE", params, where),
            self._require_returning(returning, "UPDATE", self.update_returning),
        )

        return QueryWithParams(query, params)

    def delete(
        self,
        *,
        table: IdentifierLike,
        where: Sequence[ConditionNode] = (),
        returning: Sequence[str] | None = None,
    ) -> QueryWithParams:
        params: list[Scalar] = []
        query = _join(
            "DELETE FROM",
            self.escape_identifier(table),
            self._build_clause("WHERE", params, where),
            self._require_returning(returning, "DELETE", self.returning),
        )

        return QueryWithParams(query, params)

    def create_table(
        self,
        *,
        table: IdentifierLike,
        columns: Sequence[Column],
        primary_keys: Sequence[str] = (),
        constraints: Sequence[TableConstraint] = (),
        if_not_exists: bool = False,
    ) -> QueryWithParams:
        if not columns:
            raise QueryError("no table columns specified")

        definitions = [self._build_column(column) for column in columns]
        primary_keys = self._primary_key_columns(columns, primary_keys)

        if primary_keys:
            definitions.append(f"PRIMARY KEY ({self._escape_all(primary_keys)})")

        definitions.extend(self._build_table_constraint(constraint) for constraint in constraints)

        query = _join(
            "CREATE TABLE",
            "IF NOT EXISTS" if if_not_exists and self.table_exists else "",
            self.escape_identifier(table),
            f"({', '.join(definitions)})",
            self._build_table_options(),
        )

        return QueryWithParams(query)

    def alter_table(self, *, table: IdentifierLike, alters: Sequence[Alter]) -> list[QueryWithParams]:
        if not alters:
            raise QueryError("no table alters specified")

        table_sql = self.escape_identifier(table)

        return [QueryWithParams(f"ALTER TABLE {table_sql} {self._build_alter(alter)}") for alter in alters]

    def drop_table(self, *, table: IdentifierLike, if_exists: bool = False) -> QueryWithParams:
        query = _join(
            "DROP TABLE",
            "IF EXISTS" if if_exists and self.table_exists else "",
            self.escape_identifier(table),
        )

        return QueryWithParams(query)

    def create_index(
        self,
        *,
        table: IdentifierLike,
        name: str,
        columns: Sequence[str],
        unique: bool = False,
        if_not_exists: bool = False,
        where: Sequence[ConditionNode] = (),
    ) -> QueryWithParams:
        if not columns:
            raise QueryError("no index columns specified")

        params: list[Scalar] = []
        query = _join(
            "CREATE",
            "UNIQUE" if unique else "",
            "INDEX",
            "IF NOT EXISTS" if if_not_exists and self.index_exists else "",
            self.escape_identifier(self._create_index_name(table, name)),
            "ON",
            self.escape_identifier(self._create_index_table(table)),
            f"({self._escape_all(columns)})",
            self._build_clause("WHERE", params, where),
        )

        # DDL statements cannot take bound parameters, so a partial index predicate gets its values as literals.
        return QueryWithParams(QueryWithParams(query, params).to_sql(self))

    def drop_index(self, *, table: IdentifierLike, name: str, if_exists: bool = False) -> QueryWithParams:
        query = _join(
            "DROP INDEX",
            "IF EXISTS" if if_exists and self.index_exists else "",
            self.escape_identifier(name if self.DROP_INDEX_ON_TABLE else _qualified(table, name)),
            f"ON {self.escape_identifier(table)}" if self.DROP_INDEX_ON_TABLE else "",
        )

        return QueryWithParams(query)

    def _create_index_name(self, table: IdentifierLike, name: str) -> IdentifierLike:
        """The index name of ``CREATE INDEX``; unqualified, as the index lives in its table's schema."""
        return name

    def _create_index_table(self, table: IdentifierLike) -> IdentifierLike:
        """The table of ``CREATE INDEX .. ON <table>``."""
        return table

    # -- select clauses -----------------------------------------------------------------------------------------

    def _build_distinct(self, distinct: Sequence[IdentifierLike] | None) -> str:
        if distinct is None:
            return ""

        if not distinct:
            return "DISTINCT"

        if not self.distinct_on:
            raise QueryError("DISTINCT ON is not supported")

        return f"DISTINCT ON ({self._escape_all(distinct)})"

    def _build_columns(self, params: list[Scalar], columns: Sequence[SelectColumn]) -> str:
        if not columns:
            return "*"

        return ", ".join(
            self._build_sub_query(params, column)
            if isinstance(column, Query)
            else self._build_reference(params, column)
            for column in columns
        )

    def _build_reference(self, params: list[Scalar], reference: TableRef) -> str:
        """A table or select list entry; a :class:`SubQuery` renders as ``(<select>) AS <alias>``."""
        if isinstance(reference, SubQuery):
            return (
                f"{self._build_sub_query(params, reference.select_query)} AS {self.escape_identifier(reference.alias)}"
            )

        return self.escape_identifier(reference)

    def _build_joins(self, params: list[Scalar], joins: Sequence[JoinClause | Sql]) -> str:
        return " ".join(self._build_join(params, join) for join in joins)

    def _build_join(self, params: list[Scalar], join: JoinClause | Sql) -> str:
        """``<join> <table> ON <conditions>``; a :class:`Sql` join is inlined with its params."""
        if isinstance(join, Sql):
            return self._build_value(params, join)

        if join.kind.lateral and not self.lateral:
            raise QueryError("LATERAL is not supported")

        sql = f"{join.kind} {self._build_reference(params, join.table)}"

        if join.conditions:
            return f"{sql} ON {self._build_conditions(params, join.conditions)}"

        if join.kind in (JoinEnum.CROSS_JOIN, JoinEnum.CROSS_JOIN_LATERAL):
            return sql

        return f"{sql} ON {self.cast_to_query(True)}"

    def _build_unions(self, params: list[Scalar], query: str, unions: Sequence[UnionClause]) -> str:
        """``(<select>) UNION (<select>) ..``."""
        return f"({query})" + "".join(
            f" {union.kind} {self._build_sub_query(params, union.select_query)}" for union in unions
        )

    def _build_group_by(self, group_by: Sequence[IdentifierLike]) -> str:
        return f"GROUP BY {self._escape_all(group_by)}" if group_by else ""

    def _build_order_by(self, order_by: Sequence[OrderBy]) -> str:
        if not order_by:
            return ""

        return "ORDER BY " + ", ".join(f"{self.escape_identifier(item.column)} {item.direction}" for item in order_by)

    def _build_limit(self, limit: int | None, offset: int | None) -> str:
        """``LIMIT n [OFFSET m]``; an offset without a limit is ignored."""
        if limit is None:
            return ""

        if offset is None:
            return f"LIMIT {limit:d}"

        return f"LIMIT {limit:d} OFFSET {offset:d}"

    # -- conditions ---------------------------------------------------------------------------------------------

    def _build_clause(self, keyword: str, params: list[Scalar], conditions: Sequence[ConditionNode]) -> str:
        """``<keyword> <conditions>`` (``WHERE``, ``HAVING``), or nothing without conditions."""
        return f"{keyword} {self._build_conditions(params, conditions)}" if conditions else ""

    def _build_conditions(self, params: list[Scalar], conditions: Sequence[ConditionNode]) -> str:
        sql = ""

        for index, condition in enumerate(conditions):
            if index > 0:
                sql += f" {condition.chain} "

            if isinstance(condition, ConditionGroup):
                sql += self._build_condition_group(params, condition)
            else:
                sql += self._build_condition(params, condition)

        return sql

    def _build_condition_group(self, params: list[Scalar], group: ConditionGroup) -> str:
        negation = "NOT " if group.negate else ""

        return f"{negation}({self._build_conditions(params, group.conditions)})"

    def _build_condition(self, params: list[Scalar], condition: Condition) -> str:
        kind = condition.kind

        if not isinstance(kind, ConditionEnum):
            return self._build_condition_operator(params, condition)

        match kind:
            case ConditionEnum.EQUALS | ConditionEnum.NOT_EQUALS:
                return self._build_condition_equals(params, condition)
            case ConditionEnum.BETWEEN | ConditionEnum.NOT_BETWEEN:
                return self._build_condition_between(params, condition)
            case ConditionEnum.LIKE | ConditionEnum.NOT_LIKE:
                return self._build_condition_like(params, condition)
            case ConditionEnum.GLOB | ConditionEnum.NOT_GLOB:
                return self._build_condition_glob(params, condition)
            case ConditionEnum.IN | ConditionEnum.NOT_IN:
                return self._build_condition_in(params, condition)
            case ConditionEnum.REGEX | ConditionEnum.NOT_REGEX:
                return self._build_condition_regex(params, condition)
            case ConditionEnum.EXISTS | ConditionEnum.NOT_EXISTS:
                return f"{kind} {self._build_value(params, condition.value)}"
            case ConditionEnum.RAW:
                return f"({self._build_value(params, condition.value)})"
            case _:
                return self._build_condition_operator(params, condition)

    def _build_condition_operator(self, params: list[Scalar], condition: Condition) -> str:
        """``<column> <operator> <value>``; a sequence value renders as ``(?, ?, ..)``."""
        return f"{self._build_column_name(condition)} {condition.kind} {self._build_value(params, condition.value)}"

    def _build_condition_equals(self, params: list[Scalar], condition: Condition) -> str:
        column = self._build_column_name(condition)

        if condition.value is None:
            return f"{column} IS {self._negation(condition)}NULL"

        cast_type = self._cast_type(condition.value) if condition.cast else None

        if cast_type is None:
            return self._build_condition_operator(params, condition)

        value = self._build_value(params, condition.value)

        return f"cast({column} AS {cast_type}) {condition.kind} cast({value} AS {cast_type})"

    def _cast_type(self, value: ConditionValue) -> str | None:
        """The type both sides of ``where_equals(..., cast=True)`` are cast to; ``None`` compares uncast."""
        match value:
            case bool():
                return self.type(TypeEnum.BOOLEAN)
            case int():
                return self.type(TypeEnum.INTEGER, 64)
            case float():
                return self.type(TypeEnum.FLOAT, 64)
            case str():
                return self.type(TypeEnum.TEXT)
            case datetime():
                return self.type(TypeEnum.DATETIME, 6)
            case _:
                return None

    def _build_condition_between(self, params: list[Scalar], condition: Condition) -> str:
        match condition.value:
            case (minimum, maximum):
                column = self._build_column_name(condition)
                low = self._build_value(params, minimum)
                high = self._build_value(params, maximum)

                return f"{column} {condition.kind} {low} AND {high}"
            case _:
                raise QueryError(f"{condition.kind} requires a (min, max) pair")

    def _build_condition_like(self, params: list[Scalar], condition: Condition) -> str:
        """``LIKE`` with backslash escapes in the pattern; case insensitive through ``lower()``."""
        column = self._build_column_name(condition)
        value = self._build_value(params, condition.value)

        if condition.case_insensitive:
            column, value = f"lower({column})", f"lower({value})"

        return f"{column} {self._negation(condition)}LIKE {value}"

    def _build_condition_glob(self, params: list[Scalar], condition: Condition) -> str:
        """``GLOB`` patterns become ``LIKE`` patterns (``*`` -> ``%``, ``?`` -> ``_``, a one-character class such as
        ``[*]`` -> the literal character); a pattern with any other character class becomes an anchored regex.

        :raises QueryError: An unterminated ``[`` class, or regex flags the engine cannot express.
        """
        tokens = _glob_tokens(self._pattern(condition))
        negated = condition.kind is ConditionEnum.NOT_GLOB
        like = _glob_to_like(tokens)

        if like is not None:
            kind = ConditionEnum.NOT_LIKE if negated else ConditionEnum.LIKE

            return self._build_condition_like(params, replace(condition, kind=kind, value=like))

        regex = replace(
            condition,
            kind=ConditionEnum.NOT_REGEX if negated else ConditionEnum.REGEX,
            value=_glob_to_regex(tokens),
            flags="is" if condition.case_insensitive else "s",
        )

        return self._build_condition_regex(params, regex)

    def _build_condition_in(self, params: list[Scalar], condition: Condition) -> str:
        """``<column> [NOT] IN (..)``; an empty list is always false (``IN``) or always true (``NOT IN``)."""
        value = condition.value

        if isinstance(value, Sequence) and not isinstance(value, str | bytes) and not value:
            return "1 = 1" if condition.kind is ConditionEnum.NOT_IN else "1 = 0"

        return self._build_condition_operator(params, condition)

    def _build_condition_regex(self, params: list[Scalar], condition: Condition) -> str:
        """``[NOT ]regexp_like(<column>, <pattern>, <flags>)`` with the flags of :meth:`_regex_flags`."""
        column = self._build_column_name(condition)
        pattern = self._build_value(params, condition.value)
        flags = self._build_value(params, self._regex_flags(condition.flags))

        return f"{self._negation(condition)}regexp_like({column}, {pattern}, {flags})"

    def _regex_flags(self, flags: str) -> str:
        """The engine's flags for the portable regex ``flags``; by default the portable flags themselves.

        :raises QueryError: A flag other than ``i``, ``m`` and ``s``.
        """
        return _portable_regex_flags(flags)

    def _build_condition_regex_operator(
        self, params: list[Scalar], condition: Condition, operator: str, negated_operator: str, flags: str
    ) -> str:
        """``<column> <operator> <pattern>``, with the (engine) ``flags`` as an inline ``(?flags)`` prefix."""
        pattern = self._pattern(condition)

        if flags:
            pattern = f"(?{flags}){pattern}"

        operator = negated_operator if condition.kind is ConditionEnum.NOT_REGEX else operator

        return f"{self._build_column_name(condition)} {operator} {self._build_value(params, pattern)}"

    def _build_column_name(self, condition: Condition) -> str:
        if condition.identifier is None:
            raise QueryError(f"{condition.kind} condition requires a column")

        return self.escape_identifier(condition.identifier)

    @staticmethod
    def _negation(condition: Condition) -> str:
        """``"NOT "`` for the negated condition kinds, else ``""``."""
        return "NOT " if condition.kind in _NEGATED_CONDITIONS else ""

    @staticmethod
    def _pattern(condition: Condition) -> str:
        if not isinstance(condition.value, str):
            raise QueryError(f"{condition.kind} requires a string pattern")

        return condition.value

    # -- values -------------------------------------------------------------------------------------------------

    def _build_value(self, params: list[Scalar], value: ConditionValue) -> str:
        """A value position: a :class:`Query` becomes a sub-select, a :class:`Sql` is inlined, a sequence becomes
        ``(<value>, ..)`` and a scalar becomes a ``?`` placeholder."""
        if isinstance(value, Query):
            return self._build_sub_query(params, value)

        if isinstance(value, Sql):
            fragment = value.to_query_with_params(self)
            params.extend(fragment.params)

            return fragment.query

        if isinstance(value, str | bytes):
            params.append(value)

            return "?"

        if isinstance(value, Sequence):
            return "(" + ", ".join(self._build_value(params, item) for item in value) + ")"

        params.append(value)

        return "?"

    def _build_sub_query(self, params: list[Scalar], query: Query) -> str:
        query_with_params = query.to_query_with_params().named_params_to_question_marks(self)
        params.extend(query_with_params.params)

        return f"({query_with_params.query})"

    def _build_set(self, params: list[Scalar], updates: Mapping[str, Value]) -> str:
        return ", ".join(
            f"{self.escape_identifier(column)} = {self._build_value(params, value)}"
            for column, value in updates.items()
        )

    def _escape_all(self, identifiers: Iterable[IdentifierLike]) -> str:
        return ", ".join(self.escape_identifier(identifier) for identifier in identifiers)

    # -- insert -------------------------------------------------------------------------------------------------

    def _build_row(self, params: list[Scalar], columns: Sequence[str], row: Mapping[str, Value]) -> str:
        """``(<value>, ..)`` in column order; a column missing from the row gets ``DEFAULT``."""
        return "(" + ", ".join(self._build_value(params, row[c]) if c in row else "DEFAULT" for c in columns) + ")"

    def _build_on_conflict(
        self,
        params: list[Scalar],
        on_conflict: OnConflict | None,
        columns: Sequence[str],
        last_insert_id: str | None,
    ) -> str:
        """``ON CONFLICT [(<columns>) | ON CONSTRAINT <name>]`` + ``DO NOTHING | DO UPDATE SET ..``.

        Empty updates set every inserted column to its ``excluded`` value. ``DO NOTHING`` without conflict columns
        has no target, so it applies to every unique constraint.

        :raises QueryError: ``DO UPDATE`` without conflict columns.
        """
        if on_conflict is None or not self.on_conflict:
            return ""

        if isinstance(on_conflict.conflict, str):
            target = f"ON CONSTRAINT {self.escape_identifier(on_conflict.conflict)}"
        elif on_conflict.conflict:
            target = f"({self._escape_all(on_conflict.conflict)})"
        elif on_conflict.updates is None:
            target = ""
        else:
            raise QueryError("ON CONFLICT DO UPDATE requires conflict columns or a constraint")

        if on_conflict.updates is None:
            return _join("ON CONFLICT", target, "DO NOTHING")

        updates = on_conflict.updates or {column: Identifier([Raw("excluded"), column]) for column in columns}

        return f"ON CONFLICT {target} DO UPDATE SET {self._build_set(params, updates)}"

    def _build_returning(self, returning: Sequence[str] | None) -> str:
        """``RETURNING <columns>`` (``RETURNING *`` when empty), or nothing for ``None``."""
        if returning is None:
            return ""

        return f"RETURNING {self._escape_all(returning) if returning else '*'}"

    def _require_returning(self, returning: Sequence[str] | None, statement: str, supported: bool) -> str:
        """:meth:`_build_returning` for a statement whose ``RETURNING`` cannot be emulated.

        :raises QueryError: ``returning`` is given but not ``supported``.
        """
        if returning is not None and not supported:
            raise QueryError(f"RETURNING is not supported on {statement}")

        return self._build_returning(returning)

    # -- table definitions --------------------------------------------------------------------------------------

    def _build_column(self, column: Column) -> str:
        """``<name> <type> [GENERATED BY DEFAULT AS IDENTITY] [NOT NULL] [DEFAULT <default>]``."""
        parts = [self.escape_identifier(column.name), self._column_type(column.type)]

        if column.generated_by_default_as_identity and self.generated_by_default_as_identity:
            parts.append("GENERATED BY DEFAULT AS IDENTITY")

        if column.not_null:
            parts.append("NOT NULL")

        if column.default is not None:
            default = column.default
            parts.append(
                f"DEFAULT {default.raw_sql(self) if isinstance(default, Sql) else self.cast_to_query(default)}"
            )

        return " ".join(parts)

    def _column_type(self, column_type: str | Type) -> str:
        return column_type if isinstance(column_type, str) else self.type(column_type.type, column_type.size)

    def _primary_key_columns(self, columns: Sequence[Column], primary_keys: Sequence[str]) -> Sequence[str]:
        """The columns of the ``PRIMARY KEY`` clause of ``CREATE TABLE``."""
        return primary_keys

    def _build_table_constraint(self, constraint: TableConstraint) -> str:
        match constraint:
            case UniqueConstraint():
                return self._build_unique_constraint(constraint)
            case ForeignKeyConstraint():
                return self._build_foreign_key_constraint(constraint)
            case Sql():
                return constraint.raw_sql(self)

    def _build_constraint_name(self, name: str | None) -> str:
        return f"CONSTRAINT {self.escape_identifier(name)}" if name else ""

    def _build_unique_constraint(self, constraint: UniqueConstraint) -> str:
        return _join(self._build_constraint_name(constraint.name), f"UNIQUE ({self._escape_all(constraint.columns)})")

    def _build_foreign_key_constraint(self, constraint: ForeignKeyConstraint) -> str:
        return _join(
            self._build_constraint_name(constraint.name),
            f"FOREIGN KEY ({self._escape_all(constraint.columns)})",
            f"REFERENCES {self.escape_identifier(constraint.reference_table)}",
            f"({self._escape_all(constraint.reference_columns)})",
            f"ON UPDATE {constraint.on_update}" if constraint.on_update else "",
            f"ON DELETE {constraint.on_delete}" if constraint.on_delete else "",
        )

    def _build_table_options(self) -> str:
        """Options appended after the column list of ``CREATE TABLE``."""
        return ""

    # -- alter table --------------------------------------------------------------------------------------------

    def _build_alter(self, alter: Alter) -> str:
        match alter:
            case AddColumn():
                return self._build_add_column(alter)
            case AlterColumn():
                return self._build_alter_column(alter)
            case RenameColumn():
                return self._build_rename_column(alter)
            case DropColumn():
                return self._build_drop_column(alter)
            case AddPrimaryKeys():
                return self._build_add_primary_keys(alter)
            case AddUniqueConstraint():
                return self._build_add_unique_constraint(alter)
            case AddForeignKeyConstraint():
                return self._build_add_foreign_key_constraint(alter)
            case DropConstraint():
                return self._build_drop_constraint(alter)
            case Sql():
                return alter.raw_sql(self)

    def _build_add_column(self, alter: AddColumn) -> str:
        return f"ADD COLUMN {self._build_column(alter)}"

    def _build_alter_column(self, alter: AlterColumn) -> str:
        return f"ALTER COLUMN {self.escape_identifier(alter.column)} {alter.sql}"

    def _build_rename_column(self, alter: RenameColumn) -> str:
        return f"RENAME COLUMN {self.escape_identifier(alter.old)} TO {self.escape_identifier(alter.new)}"

    def _build_drop_column(self, alter: DropColumn) -> str:
        return f"DROP COLUMN {self.escape_identifier(alter.column)}"

    def _build_add_primary_keys(self, alter: AddPrimaryKeys) -> str:
        return f"ADD PRIMARY KEY ({self._escape_all(alter.columns)})"

    def _build_add_unique_constraint(self, alter: AddUniqueConstraint) -> str:
        return f"ADD {self._build_unique_constraint(alter)}"

    def _build_add_foreign_key_constraint(self, alter: AddForeignKeyConstraint) -> str:
        return f"ADD {self._build_foreign_key_constraint(alter)}"

    def _build_drop_constraint(self, alter: DropConstraint) -> str:
        return f"DROP CONSTRAINT {self.escape_identifier(alter.constraint)}"

    # -- transactions -------------------------------------------------------------------------------------------

    def begin_transaction(self) -> QueryWithParams:
        return QueryWithParams("BEGIN TRANSACTION")

    def commit_transaction(self) -> QueryWithParams:
        return QueryWithParams("COMMIT")

    def rollback_transaction(self) -> QueryWithParams:
        return QueryWithParams("ROLLBACK")

    def begin_savepoint(self, name: str) -> QueryWithParams:
        return QueryWithParams(f"SAVEPOINT {self.escape_identifier(name)}")

    def commit_savepoint(self, name: str) -> QueryWithParams:
        return QueryWithParams(f"RELEASE SAVEPOINT {self.escape_identifier(name)}")

    def rollback_savepoint(self, name: str) -> QueryWithParams:
        return QueryWithParams(f"ROLLBACK TO {self.escape_identifier(name)}")

    # -- escaping -----------------------------------------------------------------------------------------------

    def escape_identifier(self, identifier: IdentifierLike | Alias) -> str:
        if isinstance(identifier, Alias):
            return f"{self.escape_identifier(identifier.identifier)} AS {self.escape_identifier(identifier.alias)}"

        if isinstance(identifier, Sql):
            return identifier.raw_sql(self)

        if isinstance(identifier, str):
            quote = self.ESCAPE_IDENTIFIER

            return quote + identifier.replace("\0", "").replace(quote, quote * 2) + quote

        return ".".join(self.escape_identifier(part) for part in identifier)

    def escape_string(self, string: str) -> str:
        """The string quoted with :attr:`ESCAPE_STRING`, which is doubled inside it, and :attr:`ESCAPE_CHARS` applied.

        The quote is never backslash escaped: a backslash escape can be read as a literal backslash (MySQL's
        ``NO_BACKSLASH_ESCAPES``, PostgreSQL's ``standard_conforming_strings``), which would end the literal early.
        """
        return self.ESCAPE_STRING + string.translate(self._string_translation) + self.ESCAPE_STRING

    @cached_property
    def _string_translation(self) -> dict[int, str]:
        quote = self.ESCAPE_STRING
        replacements = {**self.ESCAPE_CHARS, quote: quote * 2}

        return str.maketrans(replacements)

    # -- value conversion ---------------------------------------------------------------------------------------

    def cast_to_driver(self, value: Scalar) -> Scalar:
        if isinstance(value, bool):
            return self.cast_bool(value)

        if isinstance(value, datetime):
            return self.cast_datetime(value)

        if isinstance(value, date):
            return value.isoformat()

        return value

    def cast_to_query(self, value: Scalar) -> str:
        if value is None:
            return "NULL"

        if isinstance(value, bool):
            if self.boolean:
                return "TRUE" if value else "FALSE"

            return str(int(value))

        if isinstance(value, int):
            return str(value)

        if isinstance(value, float):
            if not math.isfinite(value):
                raise QueryError(f"{value!r} has no SQL literal")

            return repr(value)

        if isinstance(value, Decimal):
            if not value.is_finite():
                raise QueryError(f"{value!r} has no SQL literal")

            return str(value)

        if isinstance(value, str):
            return self.escape_string(value)

        if isinstance(value, bytes):
            return self._bytes_literal(value)

        if isinstance(value, datetime):
            return self.escape_string(self.cast_datetime(value))

        return self.escape_string(value.isoformat())

    def _bytes_literal(self, value: bytes) -> str:
        """A binary literal; the SQL standard ``X'..'`` form by default."""
        return f"X'{value.hex()}'"

    def cast_bool(self, value: bool) -> bool | int:
        return value if self.boolean else int(value)

    def cast_datetime(self, value: datetime) -> str:
        """:attr:`DATETIME_FORMAT`; an aware datetime is converted to UTC first, as the engine stores no offset."""
        if value.utcoffset() is not None:
            value = value.astimezone(UTC)

        return value.strftime(self.DATETIME_FORMAT)

    def parse_bool(self, value: Scalar) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, int | float | Decimal):
            return value > 0

        if isinstance(value, bytes):
            return int.from_bytes(value, "big") > 0

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in _TRUE_STRINGS:
                return True

            try:
                return float(normalized) > 0
            except ValueError:
                return False

        return value is not None

    def parse_datetime(self, string: str) -> datetime | None:
        try:
            return datetime.strptime(string, self.DATETIME_FORMAT)
        except ValueError:
            pass

        try:
            return datetime.fromisoformat(string)
        except ValueError:
            return None

    def type(self, type: TypeEnum, size: int | None = None) -> str:
        bits = size or 0

        match type:
            case TypeEnum.BOOLEAN:
                return "BOOLEAN" if self.boolean else "INTEGER"
            case TypeEnum.INTEGER:
                return "BIGINT" if bits > 32 else "INTEGER"
            case TypeEnum.FLOAT:
                return "DECIMAL(30, 15)" if bits > 32 else "DECIMAL(15, 7)"
            case TypeEnum.VARCHAR:
                return f"VARCHAR({255 if size is None else size})"
            case TypeEnum.TEXT:
                return "TEXT"
            case TypeEnum.DATETIME:
                return "DATETIME"

    # -- feature flags ------------------------------------------------------------------------------------------

    @property
    def boolean(self) -> bool:
        return self.BOOLEAN

    @property
    def distinct_on(self) -> bool:
        return self.DISTINCT_ON

    @property
    def generated_by_default_as_identity(self) -> bool:
        return self.GENERATED_BY_DEFAULT_AS_IDENTITY

    @property
    def index_exists(self) -> bool:
        return self.INDEX_EXISTS

    @property
    def lateral(self) -> bool:
        return self.LATERAL

    @property
    def on_conflict(self) -> bool:
        return self.ON_CONFLICT

    @property
    def returning(self) -> bool:
        return self.RETURNING

    @property
    def update_returning(self) -> bool:
        return self.returning

    @property
    def savepoints(self) -> bool:
        return self.SAVEPOINTS

    @property
    def table_exists(self) -> bool:
        return self.TABLE_EXISTS


__all__ = ["SQLDialect"]
