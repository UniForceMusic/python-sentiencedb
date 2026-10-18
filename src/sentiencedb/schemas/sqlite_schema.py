"""SQLite introspection (``PRAGMA`` based)."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..queries.enums import TypeEnum
from ..queries.helpers import raw
from ..queries.objects.ddl import Column, Type, UniqueConstraint
from . import _helpers
from .schema_abc import SchemaABC

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..queries.objects.ddl import ForeignKeyConstraint, Index

_AUTOINCREMENT = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)

_INDEXES = """
SELECT il.name AS index_name, il."unique" AS is_unique, ii.name AS column_name
FROM pragma_index_list(?) AS il
JOIN pragma_index_info(il.name) AS ii
WHERE il.origin = ?
ORDER BY il.name, ii.seqno
"""

_FOREIGN_KEY_CONSTRAINTS = """
SELECT id, NULL AS constraint_name, "from" AS column_name, NULL AS reference_schema, "table" AS reference_table,
    "to" AS reference_column, on_update AS update_rule, on_delete AS delete_rule
FROM pragma_foreign_key_list(?)
ORDER BY id DESC, seq
"""


class SQLiteSchema(SchemaABC):
    """SQLite introspection through the table valued ``pragma_*`` functions (SQLite >= 3.16), with the table name
    bound as a parameter.

    SQLite keeps no constraint names: unique and foreign key constraints are unnamed. Defaults are returned as
    :class:`Raw` SQL.
    """

    def schemas(self, database: DatabaseABC, dialect: DialectABC) -> list[str]:
        """The file of every attached database (empty for in-memory and temporary databases)."""
        return [row["file"] for row in database.query("SELECT file FROM pragma_database_list ORDER BY seq")]

    def tables(self, database: DatabaseABC, dialect: DialectABC) -> list[str]:
        rows = (
            database.select("sqlite_master")
            .columns(["name"])
            .where_equals("type", "table")
            .where_operator(raw("substr(name, 1, 7)"), "<>", "sqlite_")
            .order_by_asc("name")
            .execute()
            .fetch_dicts()
        )

        return [row["name"] for row in rows]

    def columns(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Column]:
        """Only an ``INTEGER PRIMARY KEY AUTOINCREMENT`` column is ``generated_by_default_as_identity``."""
        sql = (
            database.select("sqlite_master")
            .columns(["sql"])
            .where_equals("type", "table")
            .where_equals("name", table)
            .execute()
            .scalar()
        )
        autoincrement = bool(sql and _AUTOINCREMENT.search(sql))
        rows = database.prepared(
            'SELECT name, type, "notnull", dflt_value, pk FROM pragma_table_info(?) ORDER BY cid', [table]
        ).fetch_dicts()

        return [self._column(row, autoincrement) for row in rows]

    def primary_keys(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[str]:
        rows = database.prepared("SELECT name FROM pragma_table_info(?) WHERE pk > 0 ORDER BY pk", [table])

        return [row["name"] for row in rows]

    def unique_constraints(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[UniqueConstraint]:
        """``UNIQUE`` column and table constraints; unique indexes made by ``CREATE UNIQUE INDEX`` are indexes."""
        return [UniqueConstraint(index.columns) for index in self._indexes(database, table, "u")]

    def foreign_key_constraints(
        self, database: DatabaseABC, dialect: DialectABC, table: str
    ) -> list[ForeignKeyConstraint]:
        """In declaration order; a foreign key without reference columns references the primary key."""
        rows = database.prepared(_FOREIGN_KEY_CONSTRAINTS, [table]).fetch_dicts()

        return [
            constraint
            if all(constraint.reference_columns)
            else replace(
                constraint,
                reference_columns=self.primary_keys(database, dialect, str(constraint.reference_table)),
            )
            for constraint in _helpers.foreign_key_constraints(rows, "id")
        ]

    def indexes(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Index]:
        """Indexes from ``CREATE INDEX`` and ``UNIQUE`` constraints (named ``sqlite_autoindex_*``); partial indexes
        without their predicate, expression indexes skipped."""
        return [*self._indexes(database, table, "c"), *self._indexes(database, table, "u")]

    def _indexes(self, database: DatabaseABC, table: str, origin: str) -> list[Index]:
        """The indexes of ``origin``: ``c`` (``CREATE INDEX``), ``u`` (``UNIQUE``) or ``pk`` (``PRIMARY KEY``)."""
        return _helpers.indexes(database.prepared(_INDEXES, [table, origin]).fetch_dicts())

    def _column(self, row: Mapping[str, Any], autoincrement: bool) -> Column:
        identity = autoincrement and row["pk"] == 1 and str(row["type"]).upper() == "INTEGER"
        default = row["dflt_value"]

        return Column(
            row["name"],
            self._type(row["type"]),
            bool(row["notnull"]),
            None if identity or default is None else raw(default),
            identity,
        )

    def _type(self, sql_type: str) -> str | Type:
        """The portable type of a declared column type, else the declared type as is."""
        parts = _helpers.split_type(sql_type)

        if parts is None:
            return sql_type

        match parts:
            case ("BOOLEAN", _):
                return Type(TypeEnum.BOOLEAN)
            case ("INTEGER", _):
                return Type(TypeEnum.INTEGER, 32)
            case ("BIGINT", _):
                return Type(TypeEnum.INTEGER, 64)
            case ("REAL", _):
                return Type(TypeEnum.FLOAT, 64)
            case ("VARCHAR", size):
                return Type(TypeEnum.VARCHAR, size)
            case ("TEXT", size):
                return Type(TypeEnum.TEXT, size)
            case ("DATETIME", size):
                return Type(TypeEnum.DATETIME, size)
            case _:
                return sql_type


__all__ = ["SQLiteSchema"]
