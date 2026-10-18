"""PostgreSQL dialect."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from ..queries.enums import TypeEnum
from .sql_dialect import SQLDialect


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
        return self.version >= 170000

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
