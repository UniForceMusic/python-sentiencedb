"""Generic ``information_schema`` introspection; the base of the MySQL and PostgreSQL schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..queries.enums import TypeEnum
from ..queries.helpers import alias, raw
from ..queries.objects.ddl import Column, Index, Type, UniqueConstraint
from . import _helpers
from .schema_abc import SchemaABC

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..queries.objects.ddl import ForeignKeyConstraint
    from ..queries.objects.sql import Sql
    from ..queries.select_query import SelectQuery


class SQLSchema(SchemaABC):
    """Introspection through the ANSI ``information_schema`` views; a reusable base for engines that have them.

    Every method works on a plain ``information_schema`` (``indexes`` falls back to the unique constraints). The
    MySQL and PostgreSQL schemas override the parts their catalogs answer better. Subclasses scope the views to the
    current database / schema with :meth:`_schema_conditions`, pick the ``information_schema.columns`` fields they
    read with ``_COLUMN_FIELDS`` and map engine types with :meth:`_type`. Defaults are returned as :class:`Raw` SQL,
    so introspected columns can be fed back into ``CREATE TABLE``.
    """

    _COLUMN_FIELDS: tuple[str, ...] = (
        "column_name",
        "data_type",
        "character_maximum_length",
        "numeric_precision",
        "datetime_precision",
        "is_nullable",
        "column_default",
        "is_identity",
    )

    def schemas(self, database: DatabaseABC, dialect: DialectABC) -> list[str]:
        rows = (
            database.select(raw("information_schema.schemata"))
            .columns({"schema_name": raw("schema_name")})
            .order_by_asc(raw("schema_name"))
            .execute()
            .fetch_dicts()
        )

        return [row["schema_name"] for row in rows]

    def tables(self, database: DatabaseABC, dialect: DialectABC, schema: str | None = None) -> list[str]:
        query = database.select(raw("information_schema.tables")).columns({"table_name": raw("table_name")})
        rows = (
            self._where_schema(query, schema=schema)
            .where_equals(raw("table_type"), "BASE TABLE")
            .order_by_asc(raw("table_name"))
            .execute()
            .fetch_dicts()
        )

        return [row["table_name"] for row in rows]

    def table_exists(self, database: DatabaseABC, dialect: DialectABC, table: str, schema: str | None = None) -> bool:
        query = database.select(raw("information_schema.tables")).columns({"table_name": raw("table_name")})

        return (
            self._where_schema(query, schema=schema)
            .where_equals(raw("table_name"), table)
            .where_equals(raw("table_type"), "BASE TABLE")
            .execute()
            .fetch_dict()
            is not None
        )

    def index_exists(
        self, database: DatabaseABC, dialect: DialectABC, table: str, name: str, schema: str | None = None
    ) -> bool:
        """``information_schema`` has no indexes: looks for a unique constraint called ``name`` in ``schema``, as
        :meth:`indexes` does."""
        query = database.select(raw("information_schema.table_constraints")).columns(
            {"constraint_name": raw("constraint_name")}
        )

        return (
            self._where_schema(query, schema=schema)
            .where_equals(raw("constraint_name"), name)
            .where_equals(raw("constraint_type"), "UNIQUE")
            .execute()
            .fetch_dict()
            is not None
        )

    def columns(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Column]:
        query = database.select(raw("information_schema.columns")).columns(
            {field: raw(field) for field in self._COLUMN_FIELDS}
        )
        rows = (
            self._where_schema(query)
            .where_equals(raw("table_name"), table)
            .order_by_asc(raw("ordinal_position"))
            .execute()
            .fetch_dicts()
        )

        return [self._column(row, dialect) for row in rows]

    def primary_keys(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[str]:
        return [column for columns in self._key_columns(database, table, "PRIMARY KEY").values() for column in columns]

    def unique_constraints(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[UniqueConstraint]:
        return [
            UniqueConstraint(columns, name) for name, columns in self._key_columns(database, table, "UNIQUE").items()
        ]

    def foreign_key_constraints(
        self, database: DatabaseABC, dialect: DialectABC, table: str
    ) -> list[ForeignKeyConstraint]:
        """Joins each foreign key column to the referenced key column at its ``position_in_unique_constraint``."""
        query = (
            database.select(alias(raw("information_schema.referential_constraints"), "rc"))
            .columns(
                {
                    "constraint_name": raw("rc.constraint_name"),
                    "column_name": raw("kcu.column_name"),
                    "reference_schema": raw("NULLIF(ref.table_schema, kcu.table_schema)"),
                    "reference_table": raw("ref.table_name"),
                    "reference_column": raw("ref.column_name"),
                    "update_rule": raw("rc.update_rule"),
                    "delete_rule": raw("rc.delete_rule"),
                }
            )
            .inner_join(
                alias(raw("information_schema.key_column_usage"), "kcu"),
                lambda join: join.on(["kcu", "constraint_schema"], ["rc", "constraint_schema"]).on(
                    ["kcu", "constraint_name"], ["rc", "constraint_name"]
                ),
            )
            .inner_join(
                alias(raw("information_schema.key_column_usage"), "ref"),
                lambda join: (
                    join.on(["ref", "constraint_schema"], ["rc", "unique_constraint_schema"])
                    .on(["ref", "constraint_name"], ["rc", "unique_constraint_name"])
                    .on(["ref", "ordinal_position"], ["kcu", "position_in_unique_constraint"])
                ),
            )
        )
        rows = (
            self._where_schema(query, "kcu")
            .where_equals(["kcu", "table_name"], table)
            .order_by_asc(["rc", "constraint_name"])
            .order_by_asc(["kcu", "ordinal_position"])
            .execute()
            .fetch_dicts()
        )

        return _helpers.foreign_key_constraints(rows)

    def indexes(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Index]:
        """``information_schema`` has no indexes; the unique constraints' indexes are the portable answer."""
        return [
            Index(constraint.name or "", constraint.columns, True)
            for constraint in self.unique_constraints(database, dialect, table)
        ]

    def _schema_conditions(self, schema: str | None = None) -> Mapping[str, Sql | str]:
        """``column = value`` pairs restricting ``information_schema`` rows to ``schema`` (``None``: the current
        database / schema)."""
        return {} if schema is None else {"table_schema": schema}

    def _where_schema(self, query: SelectQuery, qualifier: str | None = None, schema: str | None = None) -> SelectQuery:
        """``query`` restricted by :meth:`_schema_conditions`, on the columns of ``qualifier`` when given."""
        for column, value in self._schema_conditions(schema).items():
            query.where_equals([qualifier, column] if qualifier else column, value)

        return query

    def _key_columns(self, database: DatabaseABC, table: str, constraint_type: str) -> dict[str, list[str]]:
        """The columns, in key order, of each ``constraint_type`` constraint on ``table``."""
        constraints = (
            self._where_schema(
                database.select(raw("information_schema.table_constraints")).columns(
                    {"constraint_name": raw("constraint_name")}
                )
            )
            .where_equals(raw("table_name"), table)
            .where_equals(raw("constraint_type"), constraint_type)
        )
        query = database.select(raw("information_schema.key_column_usage")).columns(
            {"constraint_name": raw("constraint_name"), "column_name": raw("column_name")}
        )
        rows = (
            self._where_schema(query)
            .where_equals(raw("table_name"), table)
            .where_in(raw("constraint_name"), constraints)
            .order_by_asc(raw("constraint_name"))
            .order_by_asc(raw("ordinal_position"))
            .execute()
            .fetch_dicts()
        )

        return {
            name: [row["column_name"] for row in group]
            for name, group in _helpers.group_rows(rows, "constraint_name").items()
        }

    def _column(self, row: Mapping[str, Any], dialect: DialectABC) -> Column:
        """A ``Column`` from a row of :attr:`_COLUMN_FIELDS`; identity columns lose their (sequence) default."""
        identity = self._is_identity(row)

        return Column(
            row["column_name"],
            self._column_type(row),
            not _true(row["is_nullable"]),
            None if identity else self._default(row, dialect),
            identity,
        )

    def _column_type(self, row: Mapping[str, Any]) -> str | Type:
        type = str(row["data_type"]).upper()
        size = next(
            (
                row[field]
                for field in ("character_maximum_length", "numeric_precision", "datetime_precision")
                if row[field] is not None
            ),
            None,
        )

        return self._type(type, size) or (type if size is None else f"{type}({size})")

    def _parse_type(self, sql_type: str) -> str | Type:
        """A full SQL type (``varchar(255)``) as a portable :class:`Type` where :meth:`_type` maps it, else as is."""
        parts = _helpers.split_type(sql_type)

        return (self._type(*parts) if parts else None) or sql_type

    def _type(self, type: str, size: int | None) -> Type | None:
        """The portable type of an upper case SQL type name and its size, or ``None`` to keep the raw type."""
        match type:
            case "FLOAT" | "DOUBLE" | "DECIMAL":
                return Type(TypeEnum.FLOAT, 64)
            case "TIMESTAMP":
                return Type(TypeEnum.DATETIME, size)
            case _:
                return _helpers.portable_type(type, size)

    def _default(self, row: Mapping[str, Any], dialect: DialectABC) -> Sql | None:
        default = row["column_default"]

        return None if default is None else raw(str(default))

    def _is_identity(self, row: Mapping[str, Any]) -> bool:
        return _true(row.get("is_identity"))


def _true(value: object) -> bool:
    """``YES``/``TRUE``/``1`` (any case, any type) as ``True``."""
    return value is not None and str(value).upper() in ("YES", "TRUE", "1")


__all__ = ["SQLSchema"]
