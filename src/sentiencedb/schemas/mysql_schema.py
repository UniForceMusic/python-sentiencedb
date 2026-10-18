"""MySQL / MariaDB introspection."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..driver import Driver
from ..queries.enums import TypeEnum
from ..queries.helpers import alias, raw
from ..queries.objects.ddl import Type
from . import _helpers
from .sql_schema import SQLSchema

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..queries.objects.ddl import ForeignKeyConstraint, Index
    from ..queries.objects.sql import Sql

_CURRENT_TIMESTAMP = re.compile(r"current_timestamp(\(\d*\))?", re.IGNORECASE)
_BIT_LITERAL = re.compile(r"b'[01]*'", re.IGNORECASE)
_HEX_LITERAL = re.compile(r"0x[0-9a-f]*", re.IGNORECASE)
_BACKSLASH_ESCAPE = re.compile(r"\\(.)", re.DOTALL)


class MySQLSchema(SQLSchema):
    """MySQL / MariaDB introspection: ``information_schema`` scoped to ``DATABASE()``, ``STATISTICS`` for indexes.

    Types are read from ``COLUMN_TYPE``, so unmapped types keep their full definition (``decimal(10,2)``,
    ``int unsigned``, ``enum(..)``).
    """

    _COLUMN_FIELDS = ("column_name", "column_type", "is_nullable", "column_default", "extra")

    def foreign_key_constraints(
        self, database: DatabaseABC, dialect: DialectABC, table: str
    ) -> list[ForeignKeyConstraint]:
        """``KEY_COLUMN_USAGE`` names the referenced column itself (the referenced unique constraint is often called
        ``PRIMARY``, in every table). Its rows without a referenced table belong to a unique key, which MySQL lets
        share its name with a foreign key."""
        query = (
            database.select(alias(raw("information_schema.key_column_usage"), "kcu"))
            .columns(
                {
                    "constraint_name": raw("kcu.constraint_name"),
                    "column_name": raw("kcu.column_name"),
                    "reference_schema": raw("NULLIF(kcu.referenced_table_schema, kcu.table_schema)"),
                    "reference_table": raw("kcu.referenced_table_name"),
                    "reference_column": raw("kcu.referenced_column_name"),
                    "update_rule": raw("rc.update_rule"),
                    "delete_rule": raw("rc.delete_rule"),
                }
            )
            .inner_join(
                alias(raw("information_schema.referential_constraints"), "rc"),
                lambda join: join.on(["rc", "constraint_schema"], ["kcu", "constraint_schema"]).on(
                    ["rc", "constraint_name"], ["kcu", "constraint_name"]
                ),
            )
        )
        rows = (
            self._where_schema(query, "kcu")
            .where_equals(["kcu", "table_name"], table)
            .where_is_not_null(["kcu", "referenced_table_name"])
            .order_by_asc(["kcu", "constraint_name"])
            .order_by_asc(["kcu", "ordinal_position"])
            .execute()
            .fetch_dicts()
        )

        return _helpers.foreign_key_constraints(rows)

    def indexes(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Index]:
        query = database.select(raw("information_schema.statistics")).columns(
            {
                "index_name": raw("index_name"),
                "column_name": raw("column_name"),
                "is_unique": raw("non_unique = 0"),
            }
        )
        rows = (
            self._where_schema(query)
            .where_equals(raw("table_name"), table)
            .where_not_equals(raw("index_name"), "PRIMARY")
            .order_by_asc(raw("index_name"))
            .order_by_asc(raw("seq_in_index"))
            .execute()
            .fetch_dicts()
        )

        return _helpers.indexes(rows)

    def index_exists(
        self, database: DatabaseABC, dialect: DialectABC, table: str, name: str, schema: str | None = None
    ) -> bool:
        """Index names are unique per table, so only ``table`` is searched."""
        query = database.select(raw("information_schema.statistics")).columns({"index_name": raw("index_name")})

        return (
            self._where_schema(query, schema=schema)
            .where_equals(raw("table_name"), table)
            .where_equals(raw("index_name"), name)
            .limit(1)
            .execute()
            .fetch_dict()
            is not None
        )

    def _schema_conditions(self, schema: str | None = None) -> Mapping[str, Sql | str]:
        return {"table_schema": raw("DATABASE()") if schema is None else schema}

    def _column_type(self, row: Mapping[str, Any]) -> str | Type:
        return self._parse_type(row["column_type"])

    def _type(self, type: str, size: int | None) -> Type | None:
        """Only ``tinyint(1)`` is a boolean; ``timestamp`` stays raw, as a portable ``DATETIME`` would lose its time
        zone conversion and range."""
        match type:
            case "TINYINT":
                return Type(TypeEnum.BOOLEAN) if size == 1 else None
            case "TIMESTAMP":
                return None
            case "INT":
                return Type(TypeEnum.INTEGER, 32)
            case "FLOAT":
                return Type(TypeEnum.FLOAT, 32)
            case "DOUBLE":
                return Type(TypeEnum.FLOAT, 64)
            case "TEXT":
                return Type(TypeEnum.TEXT, 65535)
            case "MEDIUMTEXT":
                return Type(TypeEnum.TEXT, 16777215)
            case "LONGTEXT":
                return Type(TypeEnum.TEXT, 4294967295)
            case "DATETIME":
                return Type(TypeEnum.DATETIME, size or 0)
            case _:
                return super()._type(type, size)

    def _default(self, row: Mapping[str, Any], dialect: DialectABC) -> Sql | None:
        """MySQL reports literal defaults unquoted, ``BIT`` and binary defaults as ``b'..'`` / ``0x..`` literals, and
        expressions (``DEFAULT_GENERATED``) without their parentheses and with their quotes backslash escaped;
        MariaDB reports SQL, with ``NULL`` for no default."""
        default = row["column_default"]

        if default is None:
            return None

        if dialect.driver is Driver.MARIADB:
            return None if default == "NULL" else raw(default)

        if _CURRENT_TIMESTAMP.fullmatch(default):
            return raw(default)

        if "DEFAULT_GENERATED" in str(row["extra"]).upper():
            expression = _BACKSLASH_ESCAPE.sub(r"\1", default)

            return raw(f"({expression})")

        column_type = str(row["column_type"]).lower()

        if (column_type.startswith("bit") and _BIT_LITERAL.fullmatch(default)) or (
            column_type.startswith(("binary", "varbinary")) and _HEX_LITERAL.fullmatch(default)
        ):
            return raw(default)

        return raw(dialect.cast_to_query(default))

    def _is_identity(self, row: Mapping[str, Any]) -> bool:
        return "AUTO_INCREMENT" in str(row["extra"]).upper()


__all__ = ["MySQLSchema"]
