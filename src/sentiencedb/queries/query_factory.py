"""Builders and introspection bound to one table (``db.table(name)``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from .type_aliases import IdentifierLike


class QueryFactory:
    """Builders and introspection bound to one table.

    Phase 2 stub (Agent B): see DESIGN.md for the complete public API.
    """

    def __init__(self, database: DatabaseABC, table: IdentifierLike) -> None:
        self._database = database
        self._table = table


__all__ = ["QueryFactory"]
