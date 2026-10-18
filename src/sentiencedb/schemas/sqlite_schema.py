"""SQLite introspection (``PRAGMA`` based)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .schema_abc import SchemaABC

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..queries.objects.ddl import Column, ForeignKeyConstraint, Index, UniqueConstraint


class SQLiteSchema(SchemaABC):
    """SQLite introspection (``PRAGMA`` based).

    Phase 3 stub: see DESIGN.md.
    """

    def schemas(self, database: DatabaseABC, dialect: DialectABC) -> list[str]:
        raise NotImplementedError

    def tables(self, database: DatabaseABC, dialect: DialectABC) -> list[str]:
        raise NotImplementedError

    def columns(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Column]:
        raise NotImplementedError

    def primary_keys(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[str]:
        raise NotImplementedError

    def unique_constraints(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[UniqueConstraint]:
        raise NotImplementedError

    def foreign_key_constraints(
        self, database: DatabaseABC, dialect: DialectABC, table: str
    ) -> list[ForeignKeyConstraint]:
        raise NotImplementedError

    def indexes(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Index]:
        raise NotImplementedError


__all__ = ["SQLiteSchema"]
