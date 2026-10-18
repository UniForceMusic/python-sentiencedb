"""SQLite dialect."""

from __future__ import annotations

from typing import ClassVar

from ..queries.enums import TypeEnum
from .sql_dialect import SQLDialect


class SQLiteDialect(SQLDialect):
    """SQLite 3."""

    BOOLEAN: ClassVar[bool] = False
    GENERATED_BY_DEFAULT_AS_IDENTITY: ClassVar[bool] = False
    INDEX_EXISTS: ClassVar[bool] = True
    LATERAL: ClassVar[bool] = False
    TABLE_EXISTS: ClassVar[bool] = True

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
