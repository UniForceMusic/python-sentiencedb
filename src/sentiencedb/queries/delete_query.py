"""``DELETE`` builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .mixins.returning_mixin import ReturningMixin
from .mixins.where_mixin import WhereMixin
from .query import Query

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike


class DeleteQuery(ReturningMixin, WhereMixin, Query):
    """``DELETE FROM <table> [WHERE ..] [RETURNING ..]``."""

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        super().__init__(database, dialect)
        self._table = table

    def to_query_with_params(self) -> QueryWithParams:
        return self._dialect.delete(table=self._table, where=self._where, returning=self._returning)


__all__ = ["DeleteQuery"]
