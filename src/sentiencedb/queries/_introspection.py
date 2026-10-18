"""Existence checks through introspection, for emulated ``IF [NOT] EXISTS`` and :class:`QueryFactory`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .objects.sql import Sql

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from .type_aliases import IdentifierLike


def table_name(table: IdentifierLike, dialect: DialectABC) -> str:
    """The unqualified name introspection knows ``table`` by: a path's last part, a :class:`Sql`'s raw SQL."""
    if isinstance(table, str):
        return table

    if isinstance(table, Sql):
        return table.raw_sql(dialect)

    return table_name(table[-1], dialect)


def table_exists(database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> bool:
    """Whether ``table`` is one of the database's tables."""
    return table_name(table, dialect) in database.information_schema_tables()


def index_exists(database: DatabaseABC, dialect: DialectABC, table: IdentifierLike, name: str) -> bool:
    """Whether an index called ``name`` exists on ``table`` or, as index names may be global, any other table."""
    tables = dict.fromkeys([table_name(table, dialect), *database.information_schema_tables()])

    return any(index.name == name for other in tables for index in database.information_schema_indexes(other))
