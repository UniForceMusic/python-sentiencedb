"""Joins of a select (PHP ``JoinsTrait``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Self

from ..enums import JoinEnum
from ..objects.alias import Alias, SubQuery
from ..objects.join import Join
from ..objects.sql import ExpressionF, Raw

if TYPE_CHECKING:
    from ..objects.clauses import JoinClause
    from ..objects.sql import Sql
    from ..select_query import SelectQuery
    from ..type_aliases import IdentifierLike, TableRef, Value

    JoinCallback = Callable[[Join], Join | None]


class JoinsMixin:
    """Joins. Each kind comes in three forms: ``<kind>(table, on)``, ``<kind>_table(table, on, alias)`` and
    ``<kind>_sub_query(select_query, alias, on)``. ``on`` receives a :class:`Join` to add ``ON`` conditions to;
    without conditions the join renders ``ON TRUE`` (cross joins render no ``ON``).
    """

    _joins: tuple[Join | Sql, ...] = ()

    def left_join(self, table: TableRef, on: JoinCallback | None = None) -> Self:
        """``LEFT JOIN table ON ..``."""
        return self._add_join(JoinEnum.LEFT_JOIN, table, on)

    def left_join_table(self, table: IdentifierLike, on: JoinCallback | None = None, alias: str | None = None) -> Self:
        """``LEFT JOIN table [AS alias] ON ..``."""
        return self.left_join(_aliased(table, alias), on)

    def left_join_sub_query(self, select_query: SelectQuery, alias: str, on: JoinCallback | None = None) -> Self:
        """``LEFT JOIN (<select>) AS alias ON ..``."""
        return self.left_join(SubQuery(select_query, alias), on)

    def left_join_lateral(self, table: TableRef, on: JoinCallback | None = None) -> Self:
        """``LEFT JOIN LATERAL table ON ..``."""
        return self._add_join(JoinEnum.LEFT_JOIN_LATERAL, table, on)

    def left_join_lateral_table(
        self, table: IdentifierLike, on: JoinCallback | None = None, alias: str | None = None
    ) -> Self:
        """``LEFT JOIN LATERAL table [AS alias] ON ..``."""
        return self.left_join_lateral(_aliased(table, alias), on)

    def left_join_lateral_sub_query(
        self, select_query: SelectQuery, alias: str, on: JoinCallback | None = None
    ) -> Self:
        """``LEFT JOIN LATERAL (<select>) AS alias ON ..``."""
        return self.left_join_lateral(SubQuery(select_query, alias), on)

    def inner_join(self, table: TableRef, on: JoinCallback | None = None) -> Self:
        """``INNER JOIN table ON ..``."""
        return self._add_join(JoinEnum.INNER_JOIN, table, on)

    def inner_join_table(self, table: IdentifierLike, on: JoinCallback | None = None, alias: str | None = None) -> Self:
        """``INNER JOIN table [AS alias] ON ..``."""
        return self.inner_join(_aliased(table, alias), on)

    def inner_join_sub_query(self, select_query: SelectQuery, alias: str, on: JoinCallback | None = None) -> Self:
        """``INNER JOIN (<select>) AS alias ON ..``."""
        return self.inner_join(SubQuery(select_query, alias), on)

    def inner_join_lateral(self, table: TableRef, on: JoinCallback | None = None) -> Self:
        """``INNER JOIN LATERAL table ON ..``."""
        return self._add_join(JoinEnum.INNER_JOIN_LATERAL, table, on)

    def inner_join_lateral_table(
        self, table: IdentifierLike, on: JoinCallback | None = None, alias: str | None = None
    ) -> Self:
        """``INNER JOIN LATERAL table [AS alias] ON ..``."""
        return self.inner_join_lateral(_aliased(table, alias), on)

    def inner_join_lateral_sub_query(
        self, select_query: SelectQuery, alias: str, on: JoinCallback | None = None
    ) -> Self:
        """``INNER JOIN LATERAL (<select>) AS alias ON ..``."""
        return self.inner_join_lateral(SubQuery(select_query, alias), on)

    def cross_join(self, table: TableRef) -> Self:
        """``CROSS JOIN table``."""
        return self._add_join(JoinEnum.CROSS_JOIN, table, None)

    def cross_join_table(self, table: IdentifierLike, alias: str | None = None) -> Self:
        """``CROSS JOIN table [AS alias]``."""
        return self.cross_join(_aliased(table, alias))

    def cross_join_sub_query(self, select_query: SelectQuery, alias: str) -> Self:
        """``CROSS JOIN (<select>) AS alias``."""
        return self.cross_join(SubQuery(select_query, alias))

    def cross_join_lateral(self, table: TableRef, on: JoinCallback | None = None) -> Self:
        """``CROSS JOIN LATERAL table [ON ..]``."""
        return self._add_join(JoinEnum.CROSS_JOIN_LATERAL, table, on)

    def cross_join_lateral_table(
        self, table: IdentifierLike, on: JoinCallback | None = None, alias: str | None = None
    ) -> Self:
        """``CROSS JOIN LATERAL table [AS alias] [ON ..]``."""
        return self.cross_join_lateral(_aliased(table, alias), on)

    def cross_join_lateral_sub_query(
        self, select_query: SelectQuery, alias: str, on: JoinCallback | None = None
    ) -> Self:
        """``CROSS JOIN LATERAL (<select>) AS alias [ON ..]``."""
        return self.cross_join_lateral(SubQuery(select_query, alias), on)

    def outer_apply(self, table: TableRef, on: JoinCallback | None = None) -> Self:
        """``OUTER APPLY``, written as ``LEFT JOIN LATERAL table ON ..``."""
        return self.left_join_lateral(table, on)

    def outer_apply_table(
        self, table: IdentifierLike, on: JoinCallback | None = None, alias: str | None = None
    ) -> Self:
        """``OUTER APPLY``, written as ``LEFT JOIN LATERAL table [AS alias] ON ..``."""
        return self.left_join_lateral_table(table, on, alias)

    def outer_apply_sub_query(self, select_query: SelectQuery, alias: str, on: JoinCallback | None = None) -> Self:
        """``OUTER APPLY``, written as ``LEFT JOIN LATERAL (<select>) AS alias ON ..``."""
        return self.left_join_lateral_sub_query(select_query, alias, on)

    def cross_apply(self, table: TableRef, on: JoinCallback | None = None) -> Self:
        """``CROSS APPLY``, written as ``INNER JOIN LATERAL table ON ..``."""
        return self.inner_join_lateral(table, on)

    def cross_apply_table(
        self, table: IdentifierLike, on: JoinCallback | None = None, alias: str | None = None
    ) -> Self:
        """``CROSS APPLY``, written as ``INNER JOIN LATERAL table [AS alias] ON ..``."""
        return self.inner_join_lateral_table(table, on, alias)

    def cross_apply_sub_query(self, select_query: SelectQuery, alias: str, on: JoinCallback | None = None) -> Self:
        """``CROSS APPLY``, written as ``INNER JOIN LATERAL (<select>) AS alias ON ..``."""
        return self.inner_join_lateral_sub_query(select_query, alias, on)

    def join(self, sql: str) -> Self:
        """A raw join clause, emitted verbatim."""
        self._joins += (Raw(sql),)

        return self

    def joinf(self, format: str, *values: Value) -> Self:
        """A printf style join clause; see :class:`ExpressionF`."""
        self._joins += (ExpressionF(format, values),)

        return self

    def _add_join(self, kind: JoinEnum, table: TableRef, on: JoinCallback | None) -> Self:
        join = Join(kind, table)

        if on is not None:
            join = on(join) or join

        self._joins += (join,)

        return self

    def _join_clauses(self) -> tuple[JoinClause | Sql, ...]:
        return tuple(join.to_join_clause() if isinstance(join, Join) else join for join in self._joins)


def _aliased(table: IdentifierLike, alias: str | None) -> TableRef:
    return Alias(table, alias) if alias else table


__all__ = ["JoinsMixin"]
