"""Builders and introspection bound to one table (``db.table(name)``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ._introspection import table_name

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from .alter_table_query import AlterTableQuery
    from .create_index_query import CreateIndexQuery
    from .create_table_query import CreateTableQuery
    from .delete_query import DeleteQuery
    from .drop_index_query import DropIndexQuery
    from .drop_table_query import DropTableQuery
    from .insert_query import InsertQuery
    from .objects.ddl import Column, ForeignKeyConstraint, Index, UniqueConstraint
    from .select_query import SelectQuery
    from .type_aliases import IdentifierLike, SelectColumn, Value
    from .update_query import UpdateQuery


class QueryFactory:
    """Builders and introspection bound to one table::

        users = db.table("users")
        users.insert({"name": "Alice"}).execute()
        rows = users.select(["id", "name"]).where_equals("name", "Alice").execute().fetch_dicts()

    Introspection covers the current schema / database only: for a schema-qualified ``["schema", "table"]`` path the
    introspection methods raise :class:`QueryError`. An :class:`Identifier` table is looked up by its name.
    """

    def __init__(self, database: DatabaseABC, table: IdentifierLike) -> None:
        self._database = database
        self._table = table

    def select(self, columns: Sequence[SelectColumn] | Mapping[str, SelectColumn] = ()) -> SelectQuery:
        """``SELECT columns FROM table``; see :meth:`SelectQuery.columns`."""
        return self._database.select(self._table).columns(columns)

    def insert(self, *rows: Mapping[str, Value]) -> InsertQuery:
        """``INSERT INTO table`` the given rows."""
        return self._database.insert(self._table).values(*rows)

    def update(self, updates: Mapping[str, Value]) -> UpdateQuery:
        """``UPDATE table SET updates``."""
        return self._database.update(self._table).set(updates)

    def delete(self) -> DeleteQuery:
        """``DELETE FROM table``."""
        return self._database.delete(self._table)

    def create(self) -> CreateTableQuery:
        """``CREATE TABLE table``."""
        return self._database.create_table(self._table)

    def alter(self) -> AlterTableQuery:
        """``ALTER TABLE table``."""
        return self._database.alter_table(self._table)

    def drop(self) -> DropTableQuery:
        """``DROP TABLE table``."""
        return self._database.drop_table(self._table)

    def create_index(self, name: str) -> CreateIndexQuery:
        """``CREATE INDEX name ON table``."""
        return self._database.create_index(self._table, name)

    def drop_index(self, name: str) -> DropIndexQuery:
        """``DROP INDEX name``."""
        return self._database.drop_index(self._table, name)

    def columns(self) -> list[Column]:
        """The table's columns."""
        return self._database.information_schema_columns(self._name())

    def primary_keys(self) -> list[str]:
        """The table's primary key columns."""
        return self._database.information_schema_primary_keys(self._name())

    def unique_constraints(self) -> list[UniqueConstraint]:
        """The table's unique constraints."""
        return self._database.information_schema_unique_constraints(self._name())

    def foreign_key_constraints(self) -> list[ForeignKeyConstraint]:
        """The table's foreign key constraints."""
        return self._database.information_schema_foreign_key_constraints(self._name())

    def indexes(self) -> list[Index]:
        """The table's indexes."""
        return self._database.information_schema_indexes(self._name())

    def _name(self) -> str:
        return table_name(self._table, self._database.dialect)


__all__ = ["QueryFactory"]
