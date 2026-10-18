"""PostgreSQL introspection."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..queries.enums import ReferentialActionEnum, TypeEnum
from ..queries.helpers import raw
from ..queries.objects.ddl import Type, UniqueConstraint
from . import _helpers
from .sql_schema import SQLSchema

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..queries.objects.ddl import Column, ForeignKeyConstraint, Index
    from ..queries.objects.sql import Sql

_NULL = re.compile(r"NULL(::[\w ]+)?", re.IGNORECASE)

_ACTIONS = {
    "a": ReferentialActionEnum.NO_ACTION,
    "r": ReferentialActionEnum.RESTRICT,
    "c": ReferentialActionEnum.CASCADE,
    "n": ReferentialActionEnum.SET_NULL,
    "d": ReferentialActionEnum.SET_DEFAULT,
}

_COLUMNS = """
SELECT a.attname AS column_name,
    format_type(a.atttypid, a.atttypmod) AS column_type,
    NOT a.attnotnull AS is_nullable,
    {column_default} AS column_default,
    {is_identity} OR (pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval(%'
        AND pg_get_serial_sequence(format('%I.%I', n.nspname, c.relname), a.attname) IS NOT NULL) AS is_identity
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = current_schema() AND c.relname = ? AND c.relkind IN ('r', 'p', 'v') AND a.attnum > 0
    AND NOT a.attisdropped
ORDER BY a.attnum
"""

_FOREIGN_KEY_CONSTRAINTS = """
SELECT con.conname AS constraint_name,
    a.attname AS column_name,
    NULLIF(ref_n.nspname, current_schema()) AS reference_schema,
    ref_c.relname AS reference_table,
    ref_a.attname AS reference_column,
    con.confupdtype AS update_rule,
    con.confdeltype AS delete_rule
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_class ref_c ON ref_c.oid = con.confrelid
JOIN pg_catalog.pg_namespace ref_n ON ref_n.oid = ref_c.relnamespace
CROSS JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(attnum, ref_attnum, position)
JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
JOIN pg_catalog.pg_attribute ref_a ON ref_a.attrelid = con.confrelid AND ref_a.attnum = k.ref_attnum
WHERE n.nspname = current_schema() AND c.relname = ? AND con.contype = 'f'
ORDER BY con.conname, k.position
"""

_KEY_COLUMNS = """
SELECT con.conname AS constraint_name,
    a.attname AS column_name
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, position)
JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
WHERE n.nspname = current_schema() AND c.relname = ? AND con.contype = ?
ORDER BY con.conname, k.position
"""

_INDEXES = """
SELECT ic.relname AS index_name,
    i.indisunique AS is_unique,
    a.attname AS column_name
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
JOIN pg_catalog.pg_class c ON c.oid = i.indrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL generate_series(0, {key_columns} - 1) AS k(position)
LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[k.position]
WHERE n.nspname = current_schema() AND c.relname = ? AND NOT i.indisprimary
ORDER BY ic.relname, k.position
"""


class PgSQLSchema(SQLSchema):
    """PostgreSQL introspection of the ``current_schema()``.

    Schemas and tables come from ``information_schema``; columns, keys, constraints and indexes from ``pg_catalog``,
    which knows exact types (``format_type``), identity columns, the column order of foreign keys referencing unique
    indexes and index key columns, and, unlike ``information_schema``, also shows the constraints of tables the role
    can only read.
    """

    def columns(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Column]:
        """Identity and ``SERIAL`` columns (a ``nextval()`` default on a sequence the column owns) are
        ``generated_by_default_as_identity`` without a default; generated columns have no default."""
        modern = dialect.version >= 120000
        query = _COLUMNS.format(
            is_identity="a.attidentity <> ''" if dialect.version >= 100000 else "FALSE",
            column_default=(
                "CASE WHEN a.attgenerated = '' THEN pg_get_expr(d.adbin, d.adrelid) END"
                if modern
                else "pg_get_expr(d.adbin, d.adrelid)"
            ),
        )
        rows = database.prepared(query, [table]).fetch_dicts()

        return [self._column(row, dialect) for row in rows]

    def primary_keys(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[str]:
        return [row["column_name"] for row in database.prepared(_KEY_COLUMNS, [table, "p"]).fetch_dicts()]

    def unique_constraints(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[UniqueConstraint]:
        rows = database.prepared(_KEY_COLUMNS, [table, "u"]).fetch_dicts()

        return [
            UniqueConstraint([row["column_name"] for row in group], name)
            for name, group in _helpers.group_rows(rows, "constraint_name").items()
        ]

    def foreign_key_constraints(
        self, database: DatabaseABC, dialect: DialectABC, table: str
    ) -> list[ForeignKeyConstraint]:
        rows = [
            {**row, "update_rule": _ACTIONS[row["update_rule"]], "delete_rule": _ACTIONS[row["delete_rule"]]}
            for row in database.prepared(_FOREIGN_KEY_CONSTRAINTS, [table]).fetch_dicts()
        ]

        return _helpers.foreign_key_constraints(rows)

    def indexes(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Index]:
        """Partial indexes are included without their predicate; ``INCLUDE`` columns are left out."""
        key_columns = "i.indnkeyatts" if dialect.version >= 110000 else "i.indnatts"

        return _helpers.indexes(database.prepared(_INDEXES.format(key_columns=key_columns), [table]).fetch_dicts())

    def _schema_conditions(self) -> Mapping[str, Sql]:
        return {"table_catalog": raw("current_database()"), "table_schema": raw("current_schema()")}

    def _column_type(self, row: Mapping[str, Any]) -> str | Type:
        return self._parse_type(row["column_type"])

    def _type(self, type: str, size: int | None) -> Type | None:
        """``timestamp with time zone`` stays raw: as a portable ``DATETIME`` it would lose its time zone."""
        match type:
            case "REAL":
                return Type(TypeEnum.FLOAT, 32)
            case "DOUBLE PRECISION":
                return Type(TypeEnum.FLOAT, 64)
            case "CHARACTER VARYING":
                return None if size is None else Type(TypeEnum.VARCHAR, size)
            case "TIMESTAMP WITHOUT TIME ZONE":
                return Type(TypeEnum.DATETIME, 6 if size is None else size)
            case _:
                return super()._type(type, size)

    def _default(self, row: Mapping[str, Any], dialect: DialectABC) -> Sql | None:
        """An explicit ``DEFAULT NULL`` (reported as ``NULL::<type>``) is no default."""
        default = super()._default(row, dialect)

        return None if default is not None and _NULL.fullmatch(default.raw_sql(dialect)) else default

    def _is_identity(self, row: Mapping[str, Any]) -> bool:
        return bool(row["is_identity"])


__all__ = ["PgSQLSchema"]
