"""The :class:`Database` facade."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from .database_abc import DatabaseABC

if TYPE_CHECKING:
    from .adapters.adapter_abc import AdapterABC
    from .dialects.dialect_abc import DialectABC
    from .driver import Driver
    from .options import DebugCallback, Options
    from .schemas.schema_abc import SchemaABC
    from .sockets.socket_abc import SocketABC


class Database(DatabaseABC):
    """The database facade.

    Phase 2 stub (Agent C): implements every :class:`DatabaseABC` method; see DESIGN.md.
    """

    def __init__(self, adapter: AdapterABC, dialect: DialectABC, schema: SchemaABC) -> None:
        self._adapter = adapter
        self._dialect = dialect
        self._schema = schema

    @classmethod
    def connect(
        cls,
        driver: Driver,
        name: str,
        socket: SocketABC | None = None,
        queries: Sequence[str] = (),
        options: Options | None = None,
        debug: DebugCallback | None = None,
    ) -> Self:
        """Connect: create the adapter, read the server version, then create the dialect and schema for it."""
        raise NotImplementedError

    @classmethod
    def connect_sqlite(
        cls,
        name: str = ":memory:",
        *,
        queries: Sequence[str] = (),
        options: Options | None = None,
        debug: DebugCallback | None = None,
    ) -> Self:
        """Open (or create) the SQLite database file ``name``."""
        raise NotImplementedError

    @classmethod
    def connect_postgresql(
        cls,
        name: str,
        *,
        host: str = "localhost",
        port: int = 5432,
        username: str | None = None,
        password: str | None = None,
        unix_socket: str | None = None,
        queries: Sequence[str] = (),
        options: Options | None = None,
        debug: DebugCallback | None = None,
    ) -> Self:
        """Connect to PostgreSQL over TCP, or through the socket directory ``unix_socket`` when given."""
        raise NotImplementedError

    @classmethod
    def connect_mysql(
        cls,
        name: str,
        *,
        host: str = "localhost",
        port: int = 3306,
        username: str | None = None,
        password: str | None = None,
        unix_socket: str | None = None,
        queries: Sequence[str] = (),
        options: Options | None = None,
        debug: DebugCallback | None = None,
    ) -> Self:
        """Connect to MySQL over TCP, or through the socket file ``unix_socket`` when given."""
        raise NotImplementedError

    @classmethod
    def connect_mariadb(
        cls,
        name: str,
        *,
        host: str = "localhost",
        port: int = 3306,
        username: str | None = None,
        password: str | None = None,
        unix_socket: str | None = None,
        queries: Sequence[str] = (),
        options: Options | None = None,
        debug: DebugCallback | None = None,
    ) -> Self:
        """Connect to MariaDB over TCP, or through the socket file ``unix_socket`` when given."""
        raise NotImplementedError


__all__ = ["Database"]
