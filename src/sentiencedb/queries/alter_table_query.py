"""``ALTER TABLE`` builder: one statement per operation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..results.result_abc import ResultABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike


class AlterTableQuery:
    """``ALTER TABLE`` builder. Compiles to one statement per operation, so it is not a :class:`Query`.

    Phase 2 stub (Agent B): see DESIGN.md for the complete public API.
    """

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        self._database = database
        self._dialect = dialect
        self._table = table

    def to_query_with_params(self) -> list[QueryWithParams]:
        raise NotImplementedError

    def to_sql(self) -> list[str]:
        raise NotImplementedError

    def execute(self, emulate_prepare: bool = False) -> list[ResultABC]:
        raise NotImplementedError


__all__ = ["AlterTableQuery"]
