"""The schema introspection interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..queries.objects.ddl import Column, ForeignKeyConstraint, Index, UniqueConstraint


class SchemaABC(ABC):
    """Reads the structure of the connected database.

    Schemas are stateless; :class:`Database` holds one and passes itself and its dialect on every call. They query
    the catalog through the database (preferably with the library's own builders) and return the same value objects
    the DDL builders accept, so introspected definitions can be fed back into ``CREATE``/``ALTER``.

    ``table`` is an unqualified table name in the connection's current database / schema.
    """

    @abstractmethod
    def schemas(self, database: DatabaseABC, dialect: DialectABC) -> list[str]:
        """Schema names (SQLite: the file of every attached database)."""

    @abstractmethod
    def tables(self, database: DatabaseABC, dialect: DialectABC) -> list[str]:
        """Base table names in the current database / schema, excluding internal tables."""

    @abstractmethod
    def columns(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Column]:
        """Columns in ordinal order. ``type`` is a :class:`Type` when it maps to a portable type, else the raw SQL
        type; ``default`` is the raw default expression string (or ``None``)."""

    @abstractmethod
    def primary_keys(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[str]:
        """Primary key column names in key order."""

    @abstractmethod
    def unique_constraints(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[UniqueConstraint]:
        """Unique constraints (not plain unique indexes, where the engine distinguishes them)."""

    @abstractmethod
    def foreign_key_constraints(
        self, database: DatabaseABC, dialect: DialectABC, table: str
    ) -> list[ForeignKeyConstraint]:
        """Foreign keys with their referenced table, columns (in matching order) and referential actions."""

    @abstractmethod
    def indexes(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Index]:
        """Indexes other than the primary key."""


__all__ = ["SchemaABC"]
