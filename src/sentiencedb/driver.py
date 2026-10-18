"""The :class:`Driver` enum: picks the adapter, dialect and schema for a database engine."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters.adapter_abc import AdapterABC
    from .dialects.dialect_abc import DialectABC
    from .options import DebugCallback, Options
    from .schemas.schema_abc import SchemaABC
    from .sockets.socket_abc import SocketABC


class Driver(StrEnum):
    """Supported database engines.

    Concrete classes are imported lazily so that ``import sentiencedb`` never imports a driver package.
    """

    MARIADB = "mariadb"
    MYSQL = "mysql"
    PGSQL = "pgsql"
    SQLITE = "sqlite"

    def adapter(
        self,
        name: str,
        socket: SocketABC | None = None,
        queries: Sequence[str] = (),
        options: Options | None = None,
        debug: DebugCallback | None = None,
    ) -> AdapterABC:
        """Create (and connect) the adapter for this driver."""
        adapter: type[AdapterABC]

        match self:
            case Driver.MARIADB | Driver.MYSQL:
                from .adapters.mysql_connector_adapter import MySQLConnectorAdapter

                adapter = MySQLConnectorAdapter
            case Driver.PGSQL:
                from .adapters.psycopg_adapter import PsycopgAdapter

                adapter = PsycopgAdapter
            case Driver.SQLITE:
                from .adapters.sqlite3_adapter import SQLite3Adapter

                adapter = SQLite3Adapter

        return adapter(self, name, socket, queries, options or {}, debug)

    def dialect(self, version: int | str, options: Options | None = None) -> DialectABC:
        """Create the dialect for this driver and server ``version``."""
        dialect: type[DialectABC]

        match self:
            case Driver.MARIADB | Driver.MYSQL:
                from .dialects.mysql_dialect import MySQLDialect

                dialect = MySQLDialect
            case Driver.PGSQL:
                from .dialects.pgsql_dialect import PgSQLDialect

                dialect = PgSQLDialect
            case Driver.SQLITE:
                from .dialects.sqlite_dialect import SQLiteDialect

                dialect = SQLiteDialect

        return dialect(self, version, options)

    def schema(self) -> SchemaABC:
        """Create the introspection schema for this driver."""
        match self:
            case Driver.MARIADB | Driver.MYSQL:
                from .schemas.mysql_schema import MySQLSchema

                return MySQLSchema()
            case Driver.PGSQL:
                from .schemas.pgsql_schema import PgSQLSchema

                return PgSQLSchema()
            case Driver.SQLITE:
                from .schemas.sqlite_schema import SQLiteSchema

                return SQLiteSchema()


__all__ = ["Driver"]
