"""``CREATE TABLE`` builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .query import Query

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike


class CreateTableQuery(Query):
    """``CREATE TABLE`` builder.

    Phase 2 stub (Agent B): see DESIGN.md for the complete public API.
    """

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        super().__init__(database, dialect)
        self._table = table

    def to_query_with_params(self) -> QueryWithParams:
        raise NotImplementedError


__all__ = ["CreateTableQuery"]
