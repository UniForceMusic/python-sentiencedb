"""``UPDATE`` builder."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Self

from .mixins.returning_mixin import ReturningMixin
from .mixins.where_mixin import WhereMixin
from .query import Query

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike, Value


class UpdateQuery(ReturningMixin, WhereMixin, Query):
    """``UPDATE <table> SET <column> = <value>, .. [WHERE ..] [RETURNING ..]``."""

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        super().__init__(database, dialect)
        self._table = table
        self._updates: Mapping[str, Value] = {}

    def set(self, updates: Mapping[str, Value]) -> Self:
        """Replace the column -> value updates; a value may be a :class:`Sql` or a sub-select."""
        self._updates = updates

        return self

    def to_query_with_params(self) -> QueryWithParams:
        return self._dialect.update(
            table=self._table, updates=self._updates, where=self._where, returning=self._returning
        )


__all__ = ["UpdateQuery"]
