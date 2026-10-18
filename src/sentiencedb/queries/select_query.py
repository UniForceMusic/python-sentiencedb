"""``SELECT`` builder."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Self

from .enums import OrderByDirectionEnum, UnionEnum
from .mixins.having_mixin import HavingMixin
from .mixins.joins_mixin import JoinsMixin
from .mixins.where_mixin import WhereMixin
from .objects.alias import Alias, SubQuery
from .objects.clauses import OrderBy, UnionClause
from .objects.sql import Identifier, Raw
from .query import Query

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike, SelectColumn, TableRef


class SelectQuery(WhereMixin, HavingMixin, JoinsMixin, Query):
    """``SELECT [DISTINCT] <columns> FROM <table> [joins] [WHERE] [GROUP BY] [HAVING] [ORDER BY] [LIMIT] [OFFSET]
    [UNION ..]``.

    :param table: A table, an :class:`Alias` or a :class:`SubQuery`.
    """

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: TableRef) -> None:
        super().__init__(database, dialect)
        self._table = table
        self._columns: tuple[SelectColumn, ...] = ()
        self._distinct: tuple[IdentifierLike, ...] | None = None
        self._group_by: tuple[IdentifierLike, ...] = ()
        self._order_by: tuple[OrderBy, ...] = ()
        self._limit: int | None = None
        self._offset: int | None = None
        self._unions: tuple[UnionClause, ...] = ()

    def columns(self, columns: str | Sequence[SelectColumn] | Mapping[str, SelectColumn]) -> Self:
        """Replace the select list (empty selects ``*``; a ``str`` is one column). A mapping aliases each column by
        its key, replacing the alias of an :class:`Alias` or :class:`SubQuery`; a select becomes a
        ``(<select>) AS <key>`` sub-select."""
        if isinstance(columns, str):
            self._columns = (columns,)
        elif isinstance(columns, Mapping):
            self._columns = tuple(_aliased(column, alias) for alias, column in columns.items())
        else:
            self._columns = tuple(columns)

        return self

    def distinct(self, on: str | Sequence[IdentifierLike] = ()) -> Self:
        """``SELECT DISTINCT``, or ``SELECT DISTINCT ON (on)`` where supported (a ``str`` is one column)."""
        self._distinct = _identifiers(on)

        return self

    def group_by(self, columns: str | Sequence[IdentifierLike]) -> Self:
        """Replace the ``GROUP BY`` columns (a ``str`` is one column)."""
        self._group_by = _identifiers(columns)

        return self

    def order_by_asc(self, column: IdentifierLike) -> Self:
        """Append ``ORDER BY column ASC``."""
        self._order_by += (OrderBy(column, OrderByDirectionEnum.ASC),)

        return self

    def order_by_desc(self, column: IdentifierLike) -> Self:
        """Append ``ORDER BY column DESC``."""
        self._order_by += (OrderBy(column, OrderByDirectionEnum.DESC),)

        return self

    def limit(self, limit: int) -> Self:
        """``LIMIT limit``; a negative limit removes it."""
        self._limit = limit if limit >= 0 else None

        return self

    def offset(self, offset: int) -> Self:
        """``OFFSET offset`` (only emitted together with a limit); zero or negative removes it."""
        self._offset = offset if offset > 0 else None

        return self

    def union(self, select_query: SelectQuery) -> Self:
        """Append ``UNION (<select>)``."""
        self._unions += (UnionClause(UnionEnum.UNION, select_query),)

        return self

    def union_all(self, select_query: SelectQuery) -> Self:
        """Append ``UNION ALL (<select>)``."""
        self._unions += (UnionClause(UnionEnum.UNION_ALL, select_query),)

        return self

    def count(self, emulate_prepare: bool = False) -> int:
        """Run ``SELECT count(*) FROM (<this select>) AS "count"`` and return the number of rows.

        Where the select list cannot change the number of rows, the inner select lists ``1`` instead: a joined
        ``SELECT *`` would otherwise give the derived table duplicate column names, which MySQL and MariaDB reject.
        With ``DISTINCT`` or ``UNION`` the select list decides the rows, so pick unique column names there.
        """
        count_query = SelectQuery(self._database, self._dialect, SubQuery(self._counted(), "count"))

        return int(count_query.columns([Raw("count(*)")]).execute(emulate_prepare).scalar() or 0)

    def _counted(self) -> SelectQuery:
        """This select, with ``1`` as its select list (and without a pointless ``ORDER BY``) where that keeps the
        number of rows."""
        if self._distinct is not None or self._unions or self._having:
            return self

        if self._order_by and self._limit is not None:
            # ORDER BY decides which rows LIMIT keeps and may refer to a select list alias.
            return self

        if not self._group_by and not all(_plain_column(column) for column in self._columns):
            # An aggregate (or any SQL) in the select list may collapse the rows into one.
            return self

        counted = copy.copy(self)
        counted._columns = (Raw("1"),)

        if self._limit is None:
            counted._order_by = ()

        return counted

    def to_query_with_params(self) -> QueryWithParams:
        return self._dialect.select(
            table=self._table,
            columns=self._columns,
            distinct=self._distinct,
            joins=self._join_clauses(),
            where=self._where,
            group_by=self._group_by,
            having=self._having,
            order_by=self._order_by,
            limit=self._limit,
            offset=self._offset,
            unions=self._unions,
        )


def _identifiers(columns: str | Sequence[IdentifierLike]) -> tuple[IdentifierLike, ...]:
    return (columns,) if isinstance(columns, str) else tuple(columns)


def _plain_column(column: SelectColumn) -> bool:
    """Whether ``column`` is a (possibly aliased or qualified) column reference."""
    if isinstance(column, Alias):
        column = column.identifier

    if isinstance(column, Identifier):
        column = column.identifier

    if isinstance(column, str):
        return True

    return isinstance(column, Sequence) and all(isinstance(part, str) for part in column)


def _aliased(column: SelectColumn, alias: str) -> SelectColumn:
    if isinstance(column, Alias):
        return Alias(column.identifier, alias)

    if isinstance(column, SubQuery):
        return SubQuery(column.select_query, alias)

    if isinstance(column, Query):
        return SubQuery(column, alias)

    return Alias(column, alias)


__all__ = ["SelectQuery"]
