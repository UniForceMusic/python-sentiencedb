"""``CREATE INDEX`` builder."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from ..results.result import Result
from ._introspection import index_exists
from .helpers import column_names
from .mixins.if_not_exists_mixin import IfNotExistsMixin
from .mixins.where_mixin import WhereMixin
from .query import Query

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..results.result_abc import ResultABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike


class CreateIndexQuery(IfNotExistsMixin, WhereMixin, Query):
    """``CREATE [UNIQUE] INDEX [IF NOT EXISTS] <name> ON <table> (<columns>) [WHERE ..]``; ``where_*`` conditions
    make a partial index."""

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike, name: str) -> None:
        super().__init__(database, dialect)
        self._table = table
        self._name = name
        self._columns: tuple[str, ...] = ()
        self._unique = False

    def columns(self, columns: str | Sequence[str]) -> Self:
        """Replace the indexed columns (a ``str`` is one column)."""
        self._columns = column_names(columns)

        return self

    def unique(self) -> Self:
        """Make it a unique index."""
        self._unique = True

        return self

    def to_query_with_params(self) -> QueryWithParams:
        return self._dialect.create_index(
            table=self._table,
            name=self._name,
            columns=self._columns,
            unique=self._unique,
            if_not_exists=self._if_not_exists and not self._emulate_if_not_exists,
            where=self._where,
        )

    def execute(self, emulate_prepare: bool = False) -> ResultABC:
        """Create the index. An emulated ``IF NOT EXISTS`` returns an empty :class:`Result` for an existing index."""
        emulate = self._emulate_if_not_exists or not self._dialect.index_exists

        if self._if_not_exists and emulate and index_exists(self._database, self._dialect, self._table, self._name):
            return Result()

        return super().execute(emulate_prepare)


__all__ = ["CreateIndexQuery"]
