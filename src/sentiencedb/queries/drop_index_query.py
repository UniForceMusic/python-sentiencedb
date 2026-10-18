"""``DROP INDEX`` builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..results.result import Result
from ._introspection import index_exists
from .mixins.if_exists_mixin import IfExistsMixin
from .query import Query

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..results.result_abc import ResultABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike


class DropIndexQuery(IfExistsMixin, Query):
    """``DROP INDEX [IF EXISTS] <name>`` (``... ON <table>`` on MySQL)."""

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike, name: str) -> None:
        super().__init__(database, dialect)
        self._table = table
        self._name = name

    def to_query_with_params(self) -> QueryWithParams:
        return self._dialect.drop_index(
            table=self._table, name=self._name, if_exists=self._if_exists and not self._emulate_if_exists
        )

    def execute(self, emulate_prepare: bool = False) -> ResultABC:
        """Drop the index. An emulated ``IF EXISTS`` returns an empty :class:`Result` for a missing index."""
        emulate = self._emulate_if_exists or not self._dialect.index_exists

        if self._if_exists and emulate and not index_exists(self._database, self._dialect, self._table, self._name):
            return Result()

        return super().execute(emulate_prepare)


__all__ = ["DropIndexQuery"]
