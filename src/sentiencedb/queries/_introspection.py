"""Existence checks for emulated ``IF [NOT] EXISTS``, and the table name :class:`QueryFactory` introspects.

Each check is one catalog query, keyed by the dialect's driver, that honours a schema-qualified ``["schema",
"table"]`` path: ``information_schema_*`` only sees the current schema / database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..driver import Driver
from ..exceptions import QueryError
from .objects.sql import Identifier, Sql

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from .type_aliases import IdentifierLike, Scalar


def split_table(table: IdentifierLike, dialect: DialectABC) -> tuple[str | None, str]:
    """``(schema, name)`` of a table: a path ``["schema", "table"]`` is qualified, an :class:`Identifier` is
    unwrapped and any other :class:`Sql` stands for its raw SQL.

    :raises QueryError: The path has more than two parts.
    """
    if isinstance(table, Identifier):
        return split_table(table.identifier, dialect)

    if isinstance(table, str):
        return None, table

    if isinstance(table, Sql):
        return None, table.raw_sql(dialect)

    parts = [split_table(part, dialect)[1] for part in table]

    if len(parts) == 1:
        return None, parts[0]

    if len(parts) == 2:
        return parts[0], parts[1]

    raise QueryError(f"cannot look up the table {'.'.join(parts)}: expected [schema, table]")


def table_name(table: IdentifierLike, dialect: DialectABC) -> str:
    """The name ``information_schema_*`` knows ``table`` by.

    :raises QueryError: ``table`` is schema-qualified; introspection only covers the current schema / database.
    """
    schema, name = split_table(table, dialect)

    if schema is not None:
        raise QueryError(f"cannot introspect {schema}.{name}: introspection only covers the current schema")

    return name


def table_exists(database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> bool:
    """Whether ``table`` exists (in its schema when qualified, else in the current schema / database)."""
    schema, name = split_table(table, dialect)
    params: list[Scalar] = [name]

    match dialect.driver:
        case Driver.SQLITE:
            master = dialect.escape_identifier([schema, "sqlite_master"]) if schema else "sqlite_master"
            sql = f"SELECT 1 FROM {master} WHERE type = 'table' AND name = ?"
        case Driver.PGSQL:
            sql = f"SELECT 1 FROM pg_catalog.pg_tables WHERE tablename = ? AND schemaname = {_schema(params, schema)}"
        case Driver.MYSQL | Driver.MARIADB:
            sql = (
                "SELECT 1 FROM information_schema.tables WHERE table_name = ? AND table_type = 'BASE TABLE'"
                f" AND table_schema = {_schema(params, schema, 'DATABASE()')}"
            )
        case _:  # pragma: no cover - every Driver is handled
            raise QueryError(f"cannot look up tables on {dialect.driver}")

    return database.prepared(sql, params).fetch_dict() is not None


def index_exists(database: DatabaseABC, dialect: DialectABC, table: IdentifierLike, name: str) -> bool:
    """Whether an index called ``name`` exists where it would clash with a new index on ``table``.

    MySQL and MariaDB name indexes per table, so only ``table`` is searched; elsewhere index names are unique per
    schema, so the table's whole schema is.
    """
    schema, table_name_ = split_table(table, dialect)
    params: list[Scalar] = []

    match dialect.driver:
        case Driver.SQLITE:
            master = dialect.escape_identifier([schema, "sqlite_master"]) if schema else "sqlite_master"
            sql = f"SELECT 1 FROM {master} WHERE type = 'index' AND name = ?"
            params.append(name)
        case Driver.PGSQL:
            params.append(name)
            sql = f"SELECT 1 FROM pg_catalog.pg_indexes WHERE indexname = ? AND schemaname = {_schema(params, schema)}"
        case Driver.MYSQL | Driver.MARIADB:
            params += [table_name_, name]
            sql = (
                "SELECT 1 FROM information_schema.statistics WHERE table_name = ? AND index_name = ?"
                f" AND table_schema = {_schema(params, schema, 'DATABASE()')} LIMIT 1"
            )
        case _:  # pragma: no cover - every Driver is handled
            raise QueryError(f"cannot look up indexes on {dialect.driver}")

    return database.prepared(sql, params).fetch_dict() is not None


def _schema(params: list[Scalar], schema: str | None, current: str = "current_schema()") -> str:
    """A placeholder bound to ``schema``, or the SQL for the current schema."""
    if schema is None:
        return current

    params.append(schema)

    return "?"


__all__ = ["index_exists", "split_table", "table_exists", "table_name"]
