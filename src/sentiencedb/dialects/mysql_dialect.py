"""MySQL and MariaDB dialect."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar

from ..driver import Driver
from ..queries.enums import TypeEnum
from ..queries.objects.ddl import Type
from ..queries.objects.query_with_params import QueryWithParams
from ..queries.objects.sql import CurrentTimestamp, Raw
from .sql_dialect import SQLDialect

if TYPE_CHECKING:
    from ..queries.objects.clauses import Condition, ConditionNode, OnConflict
    from ..queries.objects.ddl import AlterColumn, Column, DropConstraint
    from ..queries.type_aliases import ConditionValue, IdentifierLike, Scalar, Value


class MySQLDialect(SQLDialect):
    """MySQL (``Driver.MYSQL``) and MariaDB (``Driver.MARIADB``).

    Assumes the default ``sql_mode`` (no ``ANSI_QUOTES``, no ``NO_BACKSLASH_ESCAPES``).
    """

    DATETIME_FORMAT: ClassVar[str] = "%Y-%m-%d %H:%M:%S.%f"
    ESCAPE_ANSI: ClassVar[bool] = False
    ESCAPE_IDENTIFIER: ClassVar[str] = "`"
    ESCAPE_STRING: ClassVar[str] = "'"
    ESCAPE_CHARS: ClassVar[Mapping[str, str]] = {
        "\\": "\\\\",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\0": "\\0",
        "\b": "\\b",
        "\x1a": "\\Z",
    }
    BOOLEAN: ClassVar[bool] = False
    DROP_INDEX_ON_TABLE: ClassVar[bool] = True
    GENERATED_BY_DEFAULT_AS_IDENTITY: ClassVar[bool] = False
    TABLE_EXISTS: ClassVar[bool] = True

    @property
    def mariadb(self) -> bool:
        """Whether this dialect targets MariaDB rather than MySQL."""
        return self.driver == Driver.MARIADB

    def update(
        self,
        *,
        table: IdentifierLike,
        updates: Mapping[str, Value],
        where: Sequence[ConditionNode] = (),
        returning: Sequence[str] | None = None,
    ) -> QueryWithParams:
        """Like :meth:`SQLDialect.update`, but without ``RETURNING``: MariaDB only supports it on ``INSERT`` and
        ``DELETE``."""
        return super().update(table=table, updates=updates, where=where, returning=None)

    def begin_transaction(self) -> QueryWithParams:
        return QueryWithParams("START TRANSACTION")

    def _cast_type(self, value: ConditionValue) -> str | None:
        match value:
            case bool():
                return "UNSIGNED"
            case int():
                return "SIGNED"
            case float():
                return "DECIMAL"
            case str():
                return "CHAR"
            case _:
                return super()._cast_type(value)

    def _build_condition_like(self, params: list[Scalar], condition: Condition) -> str:
        """``LIKE BINARY`` when case sensitive; plain ``LIKE`` follows the (case insensitive) column collation."""
        if self.version < 40000:
            return super()._build_condition_like(params, condition)

        operator = "LIKE" if condition.case_insensitive else "LIKE BINARY"
        value = self._build_value(params, condition.value)

        return f"{self._build_column_name(condition)} {self._negation(condition)}{operator} {value}"

    def _build_condition_regex(self, params: list[Scalar], condition: Condition) -> str:
        """``regexp_like()`` on MySQL >= 8.0 unless the ``use_regexp`` option is set; otherwise ``REGEXP``."""
        if not self.mariadb and self.version >= 80000 and not self.options.get("use_regexp", False):
            return super()._build_condition_regex(params, condition)

        return self._build_condition_regex_operator(params, condition, "REGEXP", "NOT REGEXP")

    def _insert_ignore(self, on_conflict: OnConflict | None, last_insert_id: str | None) -> bool:
        """``DO NOTHING`` without a ``last_insert_id`` column is expressed as ``INSERT IGNORE``."""
        return self.on_conflict and on_conflict is not None and on_conflict.updates is None and not last_insert_id

    def _build_insert_into(self, on_conflict: OnConflict | None, last_insert_id: str | None) -> str:
        return "INSERT IGNORE INTO" if self._insert_ignore(on_conflict, last_insert_id) else "INSERT INTO"

    def _build_on_conflict(
        self,
        params: list[Scalar],
        on_conflict: OnConflict | None,
        columns: Sequence[str],
        last_insert_id: str | None,
    ) -> str:
        """``ON DUPLICATE KEY UPDATE ..``; the conflict target is implied by the table's unique keys.

        Empty updates set every inserted column to its new ``values()``. With ``last_insert_id`` the column is also
        set to ``last_insert_id(<column>)``, so ``LAST_INSERT_ID()`` returns the id of an existing row.
        """
        if on_conflict is None or not self.on_conflict or self._insert_ignore(on_conflict, last_insert_id):
            return ""

        updates: dict[str, Value] = {}

        if on_conflict.updates is not None:
            updates = dict(on_conflict.updates) or {
                column: Raw(f"values({self.escape_identifier(column)})") for column in columns
            }

        if last_insert_id:
            updates[last_insert_id] = Raw(f"last_insert_id({self.escape_identifier(last_insert_id)})")

        return f"ON DUPLICATE KEY UPDATE {self._build_set(params, updates)}"

    def _build_column(self, column: Column) -> str:
        """Identity columns get ``AUTO_INCREMENT``; ``DEFAULT CURRENT_TIMESTAMP`` on ``DATETIME(p)`` gets the
        column's precision, as MySQL requires."""
        column_type = column.type

        if (
            isinstance(column_type, Type)
            and column_type.type is TypeEnum.DATETIME
            and column_type.size
            and isinstance(column.default, CurrentTimestamp)
        ):
            column = replace(column, default=CurrentTimestamp(column_type.size))

        sql = super()._build_column(column)

        return f"{sql} AUTO_INCREMENT" if column.generated_by_default_as_identity else sql

    def _primary_key_columns(self, columns: Sequence[Column], primary_keys: Sequence[str]) -> Sequence[str]:
        """``AUTO_INCREMENT`` columns must be keys, so identity columns join the primary key."""
        identities = [column.name for column in columns if column.generated_by_default_as_identity]

        return list(dict.fromkeys([*primary_keys, *identities]))

    def _build_alter_column(self, alter: AlterColumn) -> str:
        return f"MODIFY {self.escape_identifier(alter.column)} {alter.sql}"

    def _build_drop_constraint(self, alter: DropConstraint) -> str:
        return f"DROP INDEX {self.escape_identifier(alter.constraint)}"

    def type(self, type: TypeEnum, size: int | None = None) -> str:
        bits = size or 0

        match type:
            case TypeEnum.BOOLEAN:
                return "TINYINT"
            case TypeEnum.FLOAT:
                return "DOUBLE" if bits > 32 else "FLOAT"
            case TypeEnum.TEXT:
                if bits > 16777215:
                    return "LONGTEXT"
                if bits > 65535:
                    return "MEDIUMTEXT"
                return "TEXT"
            case TypeEnum.DATETIME:
                return f"DATETIME({bits})" if bits > 0 else "DATETIME"
            case _:
                return super().type(type, size)

    @property
    def lateral(self) -> bool:
        return not self.mariadb and self.version >= 80014

    @property
    def on_conflict(self) -> bool:
        return self.mariadb or self.version >= 40100

    @property
    def returning(self) -> bool:
        return self.mariadb and self.version >= 100500

    @property
    def table_exists(self) -> bool:
        return self.version >= 32300


__all__ = ["MySQLDialect"]
