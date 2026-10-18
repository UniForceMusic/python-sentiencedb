"""``DROP TABLE`` builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..results.result import Result
from ._introspection import table_exists
from .mixins.if_exists_mixin import IfExistsMixin
from .query import Query

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..results.result_abc import ResultABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike


class DropTableQuery(IfExistsMixin, Query):
    """``DROP TABLE [IF EXISTS] <table>``."""

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        super().__init__(database, dialect)
        self._table = table

    def to_query_with_params(self) -> QueryWithParams:
        return self._dialect.drop_table(table=self._table, if_exists=self._if_exists and not self._emulate_if_exists)

    def execute(self, emulate_prepare: bool = False) -> ResultABC:
        """Drop the table. An emulated ``IF EXISTS`` returns an empty :class:`Result` for a missing table."""
        emulate = self._emulate_if_exists or not self._dialect.table_exists

        if self._if_exists and emulate and not table_exists(self._database, self._dialect, self._table):
            return Result()

        return super().execute(emulate_prepare)


__all__ = ["DropTableQuery"]
