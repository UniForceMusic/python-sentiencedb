"""Builder tables as ``(schema, name)`` for the database's ``information_schema_*`` methods.

The emulated ``IF [NOT] EXISTS`` checks honour a schema-qualified ``["schema", "table"]`` path; the per-table
introspection of :class:`QueryFactory` only covers the current schema / database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..exceptions import QueryError
from .objects.sql import Identifier, Sql

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from .type_aliases import IdentifierLike


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
    """Whether ``table`` exists (in its schema when qualified); see
    :meth:`DatabaseABC.information_schema_table_exists`."""
    schema, name = split_table(table, dialect)

    return database.information_schema_table_exists(name, schema)


def index_exists(database: DatabaseABC, dialect: DialectABC, table: IdentifierLike, name: str) -> bool:
    """Whether an index ``name`` clashes with a new index on ``table``; see
    :meth:`DatabaseABC.information_schema_index_exists`."""
    schema, table_name_ = split_table(table, dialect)

    return database.information_schema_index_exists(table_name_, name, schema)


__all__ = ["index_exists", "split_table", "table_exists", "table_name"]
