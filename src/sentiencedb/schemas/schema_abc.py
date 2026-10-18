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

    ``table`` is an unqualified table name. ``schema`` names the database / schema to look in (SQLite: an attached
    database such as ``main`` or ``temp``); ``None`` means the connection's current one. The per-table methods only
    cover the current database / schema.
    """

    @abstractmethod
    def schemas(self, database: DatabaseABC, dialect: DialectABC) -> list[str]:
        """Schema names (SQLite: the file of every attached database)."""

    @abstractmethod
    def tables(self, database: DatabaseABC, dialect: DialectABC, schema: str | None = None) -> list[str]:
        """Base table names in ``schema``, excluding internal tables."""

    @abstractmethod
    def table_exists(self, database: DatabaseABC, dialect: DialectABC, table: str, schema: str | None = None) -> bool:
        """Whether the base table ``table`` exists in ``schema`` (one catalog query; emulates ``IF [NOT] EXISTS``)."""

    @abstractmethod
    def index_exists(
        self, database: DatabaseABC, dialect: DialectABC, table: str, name: str, schema: str | None = None
    ) -> bool:
        """Whether an index called ``name`` exists where it would clash with a new index on ``table`` in ``schema``
        (one catalog query; emulates ``IF [NOT] EXISTS``).

        Where index names are unique per schema (PostgreSQL, SQLite) the whole schema is searched, whatever
        ``table`` is; MySQL and MariaDB name indexes per table, so only ``table`` is.
        """

    @abstractmethod
    def columns(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Column]:
        """Columns in ordinal order. ``type`` is a :class:`Type` when it maps to a portable type, else the raw SQL
        type; ``default`` is the default expression as :class:`Raw` SQL (or ``None`` without a default)."""

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
