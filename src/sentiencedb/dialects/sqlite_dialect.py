"""SQLite dialect."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from ..exceptions import QueryError
from ..queries.enums import TypeEnum
from .sql_dialect import SQLDialect, _split_path

if TYPE_CHECKING:
    from ..queries.objects.clauses import Condition, OnConflict, UnionClause
    from ..queries.objects.ddl import (
        AddForeignKeyConstraint,
        AddPrimaryKeys,
        AddUniqueConstraint,
        AlterColumn,
        Column,
        DropColumn,
        DropConstraint,
        RenameColumn,
    )
    from ..queries.type_aliases import ConditionValue, IdentifierLike, Scalar, Value

_LIKE_TOKEN = re.compile(r"\\(.)|[%_*?\[\]]", re.DOTALL)


def _like_to_glob(pattern: str) -> str:
    """Translate a ``LIKE`` pattern with backslash escapes into an equivalent ``GLOB`` pattern."""

    def replace(match: re.Match[str]) -> str:
        escaped = match[1]

        if escaped is None and match[0] in "%_":
            return "*" if match[0] == "%" else "?"

        literal = match[0] if escaped is None else escaped

        return f"[{literal}]" if literal in "*?[]" else literal

    return _LIKE_TOKEN.sub(replace, pattern)


_STRICT_TYPES = {
    TypeEnum.BOOLEAN: "INTEGER",
    TypeEnum.INTEGER: "INTEGER",
    TypeEnum.FLOAT: "REAL",
    TypeEnum.VARCHAR: "TEXT",
    TypeEnum.TEXT: "TEXT",
    TypeEnum.DATETIME: "TEXT",
}
"""Column types of ``STRICT`` tables, which only accept ``INT``, ``INTEGER``, ``REAL``, ``TEXT``, ``BLOB`` and
``ANY``."""


class SQLiteDialect(SQLDialect):
    """SQLite 3."""

    BOOLEAN: ClassVar[bool] = False
    GENERATED_BY_DEFAULT_AS_IDENTITY: ClassVar[bool] = False
    LATERAL: ClassVar[bool] = False

    def _build_unions(self, params: list[Scalar], query: str, unions: Sequence[UnionClause]) -> str:
        """SQLite rejects parenthesised compound members, so each member is wrapped as ``SELECT * FROM (<select>)``,
        which also keeps a member's own ``ORDER BY`` / ``LIMIT``."""
        return f"SELECT * FROM ({query})" + "".join(
            f" {union.union} SELECT * FROM {self._build_sub_query(params, union.select_query)}" for union in unions
        )

    def _build_condition_like(self, params: list[Scalar], condition: Condition) -> str:
        """Case sensitive ``LIKE`` becomes ``GLOB`` (SQLite's ``LIKE`` ignores ASCII case); case insensitive ``LIKE``
        declares the backslash as its escape character, and compares through ``lower()`` when the
        ``deprecated_case_sensitive_like`` option makes ``LIKE`` case sensitive."""
        column = self._build_column_name(condition)
        negation = self._negation(condition)

        if condition.case_insensitive:
            value = self._build_value(params, condition.value)

            if self.options.get("deprecated_case_sensitive_like", False):
                column, value = f"lower({column})", f"lower({value})"

            return f"{column} {negation}LIKE {value} ESCAPE '\\'"

        return f"{column} {negation}GLOB {self._build_value(params, _like_to_glob(self._pattern(condition)))}"

    def _build_condition_glob(self, params: list[Scalar], condition: Condition) -> str:
        """Native ``GLOB``; case insensitive through ``lower()``."""
        column = self._build_column_name(condition)
        value = self._build_value(params, condition.value)

        if condition.case_insensitive:
            column, value = f"lower({column})", f"lower({value})"

        return f"{column} {self._negation(condition)}GLOB {value}"

    def _build_condition_regex(self, params: list[Scalar], condition: Condition) -> str:
        """``regexp_like()`` (registered by the adapter), or ``REGEXP`` with the ``use_regexp`` option."""
        if not self.options.get("use_regexp", False):
            return super()._build_condition_regex(params, condition)

        flags = self._regex_flags(condition.flags)

        return self._build_condition_regex_operator(params, condition, "REGEXP", "NOT REGEXP", flags)

    def _cast_type(self, value: ConditionValue) -> str | None:
        """Datetimes compare as ``TEXT``: a ``DATETIME`` cast has numeric affinity and keeps only the year."""
        return "TEXT" if isinstance(value, datetime) else super()._cast_type(value)

    def _build_row(self, params: list[Scalar], columns: Sequence[str], row: Mapping[str, Value]) -> str:
        """SQLite has no ``DEFAULT`` keyword inside ``VALUES``.

        :raises QueryError: The row lacks one of the insert's columns.
        """
        if any(column not in row for column in columns):
            raise QueryError(
                "SQLite does not support DEFAULT in VALUES: every row of a multi-row insert must have the same columns"
            )

        return super()._build_row(params, columns, row)

    def _build_on_conflict(
        self,
        params: list[Scalar],
        on_conflict: OnConflict | None,
        columns: Sequence[str],
        last_insert_id: str | None,
    ) -> str:
        if on_conflict is not None and isinstance(on_conflict.conflict, str):
            raise QueryError("SQLite does not support named constraints")

        return super()._build_on_conflict(params, on_conflict, columns, last_insert_id)

    def _build_column(self, column: Column) -> str:
        """Identity columns are ``INTEGER PRIMARY KEY AUTOINCREMENT``, whatever their declared type."""
        if column.generated_by_default_as_identity:
            return f"{self.escape_identifier(column.name)} INTEGER PRIMARY KEY AUTOINCREMENT"

        return super()._build_column(column)

    def _primary_key_columns(self, columns: Sequence[Column], primary_keys: Sequence[str]) -> Sequence[str]:
        """Identity columns are declared primary key inline, so they leave the ``PRIMARY KEY`` clause."""
        identities = {column.name for column in columns if column.generated_by_default_as_identity}

        return [key for key in primary_keys if key not in identities]

    def _build_constraint_name(self, name: str | None) -> str:
        return ""

    def _build_table_options(self) -> str:
        """``STRICT`` with the ``strict`` option.

        :raises QueryError: ``strict`` before SQLite 3.37.0.
        """
        if not self.options.get("strict", False):
            return ""

        if self.version < 33700:
            raise QueryError("SQLite does not support STRICT tables before 3.37.0")

        return "STRICT"

    def _create_index_name(self, table: IdentifierLike, name: str) -> IdentifierLike:
        """SQLite qualifies the index name with the schema: ``CREATE INDEX "s"."i" ON "t"``."""
        schema, _ = _split_path(table)

        return [*schema, name] if schema else name

    def _create_index_table(self, table: IdentifierLike) -> IdentifierLike:
        return _split_path(table)[1]

    def _build_alter_column(self, alter: AlterColumn) -> str:
        raise QueryError("SQLite does not support altering columns")

    def _build_rename_column(self, alter: RenameColumn) -> str:
        if self.version < 32500:
            raise QueryError("SQLite does not support renaming columns before 3.25.0")

        return super()._build_rename_column(alter)

    def _build_drop_column(self, alter: DropColumn) -> str:
        if self.version < 33500:
            raise QueryError("SQLite does not support dropping columns before 3.35.0")

        return super()._build_drop_column(alter)

    def _build_add_primary_keys(self, alter: AddPrimaryKeys) -> str:
        raise QueryError("SQLite does not support adding primary keys by altering the table")

    def _build_add_unique_constraint(self, alter: AddUniqueConstraint) -> str:
        raise QueryError("SQLite does not support adding constraints by altering the table")

    def _build_add_foreign_key_constraint(self, alter: AddForeignKeyConstraint) -> str:
        raise QueryError("SQLite does not support adding constraints by altering the table")

    def _build_drop_constraint(self, alter: DropConstraint) -> str:
        raise QueryError("SQLite does not support dropping constraints by altering the table")

    def type(self, type: TypeEnum, size: int | None = None) -> str:
        """With the ``strict`` option, the types a ``STRICT`` table accepts (:data:`_STRICT_TYPES`)."""
        if self.options.get("strict", False):
            return _STRICT_TYPES[type]

        match type:
            case TypeEnum.BOOLEAN:
                return "BOOLEAN"
            case TypeEnum.FLOAT:
                return "REAL"
            case _:
                return super().type(type, size)

    @property
    def index_exists(self) -> bool:
        return self.version >= 30300

    @property
    def on_conflict(self) -> bool:
        return self.version >= 32400

    @property
    def returning(self) -> bool:
        return self.version >= 33500

    @property
    def table_exists(self) -> bool:
        return self.version >= 30300


__all__ = ["SQLiteDialect"]
