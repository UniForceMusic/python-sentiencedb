"""Module level functions that connect and return a :class:`Database`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .database import Database
from .driver import Driver
from .sockets.network_socket import NetworkSocket
from .sockets.unix_socket import UnixSocket

if TYPE_CHECKING:
    from .options import DebugCallback, Options
    from .sockets.socket_abc import SocketABC


def connect(
    driver: Driver,
    name: str,
    socket: SocketABC | None = None,
    queries: Sequence[str] = (),
    options: Options | None = None,
    debug: DebugCallback | None = None,
) -> Database:
    """Connect: create the adapter, read the server version, then create the dialect and schema for it.

    :param driver: The database engine.
    :param name: Database name, or the SQLite file path (``":memory:"`` for an in-memory database).
    :param socket: Server location and credentials; required for every engine but SQLite.
    :param queries: Statements run on every new connection (each thread has its own).
    :param options: Connection and dialect options.
    :param debug: Called after every statement with the interpolated SQL, start time and error message.
    :raises AdapterError: Invalid configuration, e.g. a missing socket.
    :raises ConnectionFailedError: The connection or one of its startup statements failed.
    """
    adapter = driver.adapter(name, socket, queries, options, debug)

    return Database(adapter, driver.dialect(adapter.version(), options), driver.schema())


def connect_sqlite(
    name: str = ":memory:",
    *,
    queries: Sequence[str] = (),
    options: Options | None = None,
    debug: DebugCallback | None = None,
) -> Database:
    """Open (or create) the SQLite database file ``name``."""
    return connect(Driver.SQLITE, name, None, queries, options, debug)


def connect_postgresql(
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
) -> Database:
    """Connect to PostgreSQL over TCP, or through the socket directory ``unix_socket`` when given."""
    socket = _socket(host, port, username, password, unix_socket)

    return connect(Driver.PGSQL, name, socket, queries, options, debug)


def connect_mysql(
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
) -> Database:
    """Connect to MySQL over TCP, or through the socket file ``unix_socket`` when given."""
    socket = _socket(host, port, username, password, unix_socket)

    return connect(Driver.MYSQL, name, socket, queries, options, debug)


def connect_mariadb(
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
) -> Database:
    """Connect to MariaDB over TCP, or through the socket file ``unix_socket`` when given."""
    socket = _socket(host, port, username, password, unix_socket)

    return connect(Driver.MARIADB, name, socket, queries, options, debug)


def _socket(host: str, port: int, username: str | None, password: str | None, unix_socket: str | None) -> SocketABC:
    if unix_socket is not None:
        return UnixSocket(unix_socket, username, password)

    return NetworkSocket(host, port, username, password)


__all__ = ["connect", "connect_mariadb", "connect_mysql", "connect_postgresql", "connect_sqlite"]
