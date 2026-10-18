"""PostgreSQL introspection."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..queries.enums import ReferentialActionEnum, TypeEnum
from ..queries.helpers import raw
from ..queries.objects.ddl import Type
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
    pg_get_expr(d.adbin, d.adrelid) AS column_default,
    {is_identity} AS is_identity
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

    Schemas, tables, primary keys and unique constraints come from ``information_schema``; columns, foreign keys
    and indexes from ``pg_catalog``, which knows exact types (``format_type``), identity columns, the column order of
    foreign keys referencing unique indexes, and index key columns.
    """

    def columns(self, database: DatabaseABC, dialect: DialectABC, table: str) -> list[Column]:
        """Identity and ``SERIAL`` columns are ``generated_by_default_as_identity`` without a default."""
        is_identity = "a.attidentity <> ''" if dialect.version >= 100000 else "FALSE"
        rows = database.prepared(_COLUMNS.format(is_identity=is_identity), [table]).fetch_dicts()

        return [self._column(row, dialect) for row in rows]

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
        return bool(row["is_identity"]) or str(row["column_default"] or "").startswith("nextval(")


__all__ = ["PgSQLSchema"]
