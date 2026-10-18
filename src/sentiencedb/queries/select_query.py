"""``SELECT`` builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Self

from .enums import OrderByDirectionEnum, UnionEnum
from .mixins.having_mixin import HavingMixin
from .mixins.joins_mixin import JoinsMixin
from .mixins.where_mixin import WhereMixin
from .objects.alias import Alias, SubQuery
from .objects.clauses import OrderBy, UnionClause
from .objects.sql import Raw
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

    def columns(self, columns: Sequence[SelectColumn] | Mapping[str, SelectColumn]) -> Self:
        """Replace the select list (empty selects ``*``). A mapping aliases each column by its key; a select
        becomes a ``(<select>) AS <key>`` sub-select."""
        if isinstance(columns, Mapping):
            self._columns = tuple(_aliased(column, alias) for alias, column in columns.items())
        else:
            self._columns = tuple(columns)

        return self

    def distinct(self, on: Sequence[IdentifierLike] = ()) -> Self:
        """``SELECT DISTINCT``, or ``SELECT DISTINCT ON (on)`` where supported."""
        self._distinct = tuple(on)

        return self

    def group_by(self, columns: Sequence[IdentifierLike]) -> Self:
        """Replace the ``GROUP BY`` columns."""
        self._group_by = tuple(columns)

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
        """Run ``SELECT count(*) FROM (<this select>) AS "count"`` and return the number of rows."""
        count_query = SelectQuery(self._database, self._dialect, SubQuery(self, "count")).columns([Raw("count(*)")])

        return int(count_query.execute(emulate_prepare).scalar() or 0)

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


def _aliased(column: SelectColumn, alias: str) -> SelectColumn:
    if isinstance(column, Alias | SubQuery):
        return column

    if isinstance(column, Query):
        return SubQuery(column, alias)

    return Alias(column, alias)


__all__ = ["SelectQuery"]
