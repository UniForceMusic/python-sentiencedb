"""PostgreSQL dialect."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from ..queries.enums import TypeEnum
from .sql_dialect import SQLDialect

if TYPE_CHECKING:
    from ..queries.objects.clauses import Condition
    from ..queries.objects.ddl import Column
    from ..queries.type_aliases import Scalar

_SERIALS = {
    "SMALLINT": "SERIAL",
    "INT": "SERIAL",
    "INT2": "SERIAL",
    "INT4": "SERIAL",
    "INTEGER": "SERIAL",
    "BIGINT": "BIGSERIAL",
    "INT8": "BIGSERIAL",
}


class PgSQLDialect(SQLDialect):
    """PostgreSQL. Assumes ``standard_conforming_strings = on`` (the default since 9.1)."""

    DATETIME_FORMAT: ClassVar[str] = "%Y-%m-%d %H:%M:%S.%f"
    DISTINCT_ON: ClassVar[bool] = True
    INDEX_EXISTS: ClassVar[bool] = True
    TABLE_EXISTS: ClassVar[bool] = True

    def cast_datetime(self, value: datetime) -> str:
        """ISO format with microseconds, keeping the UTC offset of aware datetimes."""
        return value.isoformat(sep=" ", timespec="microseconds")

    def _bytes_literal(self, value: bytes) -> str:
        return f"'\\x{value.hex()}'::bytea"

    def _build_condition_like(self, params: list[Scalar], condition: Condition) -> str:
        """``LIKE``, or ``ILIKE`` when case insensitive."""
        operator = "ILIKE" if condition.case_insensitive else "LIKE"
        value = self._build_value(params, condition.value)

        return f"{self._build_column_name(condition)} {self._negation(condition)}{operator} {value}"

    def _build_condition_regex(self, params: list[Scalar], condition: Condition) -> str:
        """``regexp_like()`` on PostgreSQL >= 15 unless the ``use_tilde_regex`` option is set; otherwise ``~``."""
        if self.version >= 150000 and not self.options.get("use_tilde_regex", False):
            return super()._build_condition_regex(params, condition)

        return self._build_condition_regex_operator(params, condition, "~", "!~")

    def _build_column(self, column: Column) -> str:
        """Without identity column support, integer identity columns become ``SERIAL``/``BIGSERIAL``."""
        if not column.generated_by_default_as_identity or self.generated_by_default_as_identity:
            return super()._build_column(column)

        column_type = self._column_type(column.type)
        serial = _SERIALS.get(column_type.upper())

        if serial is not None:
            column_type = serial.lower() if column_type.islower() else serial

        return super()._build_column(replace(column, type=column_type, generated_by_default_as_identity=False))

    def type(self, type: TypeEnum, size: int | None = None) -> str:
        match type:
            case TypeEnum.FLOAT:
                return "DOUBLE PRECISION" if (size or 0) > 32 else "REAL"
            case TypeEnum.DATETIME:
                return "TIMESTAMP"
            case _:
                return super().type(type, size)

    @property
    def distinct_on(self) -> bool:
        return self.version >= 70200

    @property
    def generated_by_default_as_identity(self) -> bool:
        """Identity columns only when ``use_serials`` is explicitly false (it defaults to true) on PostgreSQL >= 10;
        otherwise auto increment columns become ``SERIAL``/``BIGSERIAL``."""
        return not self.options.get("use_serials", True) and self.version >= 100000

    @property
    def index_exists(self) -> bool:
        return self.version >= 90500

    @property
    def lateral(self) -> bool:
        return self.version >= 90300

    @property
    def on_conflict(self) -> bool:
        return self.version >= 90500

    @property
    def returning(self) -> bool:
        return self.version >= 80200

    @property
    def table_exists(self) -> bool:
        return self.version >= 90100


__all__ = ["PgSQLDialect"]
