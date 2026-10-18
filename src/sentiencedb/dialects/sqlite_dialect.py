"""SQLite dialect."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar

from ..exceptions import QueryError
from ..queries.enums import TypeEnum
from .sql_dialect import SQLDialect

if TYPE_CHECKING:
    from ..queries.objects.clauses import Condition, OnConflict
    from ..queries.objects.ddl import (
        AddForeignKeyConstraint,
        AddPrimaryKeys,
        AddUniqueConstraint,
        AlterColumn,
        Column,
        DropConstraint,
        RenameColumn,
    )
    from ..queries.type_aliases import Scalar

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


class SQLiteDialect(SQLDialect):
    """SQLite 3."""

    BOOLEAN: ClassVar[bool] = False
    GENERATED_BY_DEFAULT_AS_IDENTITY: ClassVar[bool] = False
    INDEX_EXISTS: ClassVar[bool] = True
    LATERAL: ClassVar[bool] = False
    TABLE_EXISTS: ClassVar[bool] = True

    def _build_condition_like(self, params: list[Scalar], condition: Condition) -> str:
        """Case sensitive ``LIKE`` becomes ``GLOB`` (SQLite's ``LIKE`` ignores ASCII case); case insensitive ``LIKE``
        declares the backslash as its escape character."""
        column = self._build_column_name(condition)
        negation = self._negation(condition)

        if condition.case_insensitive:
            return f"{column} {negation}LIKE {self._build_value(params, condition.value)} ESCAPE '\\'"

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

        return self._build_condition_regex_operator(params, condition, "REGEXP", "NOT REGEXP")

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
        return "STRICT" if self.options.get("strict", False) else ""

    def _build_alter_column(self, alter: AlterColumn) -> str:
        raise QueryError("SQLite does not support altering columns")

    def _build_rename_column(self, alter: RenameColumn) -> str:
        if self.version < 32500:
            raise QueryError("SQLite does not support renaming columns before 3.25.0")

        return super()._build_rename_column(alter)

    def _build_add_primary_keys(self, alter: AddPrimaryKeys) -> str:
        raise QueryError("SQLite does not support adding primary keys by altering the table")

    def _build_add_unique_constraint(self, alter: AddUniqueConstraint) -> str:
        raise QueryError("SQLite does not support adding constraints by altering the table")

    def _build_add_foreign_key_constraint(self, alter: AddForeignKeyConstraint) -> str:
        raise QueryError("SQLite does not support adding constraints by altering the table")

    def _build_drop_constraint(self, alter: DropConstraint) -> str:
        raise QueryError("SQLite does not support dropping constraints by altering the table")

    def type(self, type: TypeEnum, size: int | None = None) -> str:
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
