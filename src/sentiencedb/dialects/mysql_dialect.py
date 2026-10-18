"""MySQL and MariaDB dialect."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from ..driver import Driver
from ..queries.enums import TypeEnum
from ..queries.objects.query_with_params import QueryWithParams
from .sql_dialect import SQLDialect


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

    def begin_transaction(self) -> QueryWithParams:
        return QueryWithParams("START TRANSACTION")

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
