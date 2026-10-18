"""MySQL / MariaDB adapter on mysql-connector-python."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from ..exceptions import AdapterError
from ..results.mysql_connector_result import MySQLConnectorResult
from ..sockets.network_socket import NetworkSocket
from ..sockets.unix_socket import UnixSocket
from ._dbapi_adapter import DBAPIAdapter
from ._errors import map_mysql_connector_error

if TYPE_CHECKING:
    from mysql.connector.abstracts import MySQLConnectionAbstract

    from ..driver import Driver
    from ..exceptions import DriverError
    from ..options import DebugCallback, Options
    from ..queries.type_aliases import Scalar
    from ..sockets.socket_abc import SocketABC


_SERVER_GONE_AWAY = 2006


class MySQLConnectorAdapter(DBAPIAdapter["MySQLConnectionAbstract"]):
    """MySQL / MariaDB adapter on mysql-connector-python.

    Connections run in autocommit mode; transactions are explicit ``START TRANSACTION`` statements. Statements with
    parameters run as server side prepared statements, which understand the dialect's ``?`` placeholders natively.
    Results are read completely before the statement returns, so the connection is immediately free for the next
    one. MySQL and MariaDB store no UTC offset, so aware datetimes are bound in UTC.

    The C extension decodes a bound ``bytes`` parameter that is selected back directly (``SELECT ?``) as text; values
    read from ``BLOB``/``BINARY`` columns, and emulated prepares, are unaffected.

    Options: ``charset``, ``collation`` and ``engine``.
    """

    DRIVER_HINT = "MySQL and MariaDB support needs mysql-connector-python: pip install 'sentiencedb[mysql]'"

    def __init__(
        self,
        driver: Driver,
        name: str,
        socket: SocketABC | None,
        queries: Sequence[str],
        options: Options,
        debug: DebugCallback | None,
    ) -> None:
        if socket is None:
            raise AdapterError("MySQL and MariaDB require a socket")

        super().__init__(driver, name, socket, queries, options, debug)

    def last_insert_id(self, name: str | None = None) -> int | str | None:
        """``LAST_INSERT_ID()``; ``None`` instead of ``0`` when the connection has not generated an id yet."""
        import mysql.connector

        try:
            value: int | None = self._execute(self._connection(), "SELECT LAST_INSERT_ID()", None).scalar()
        except mysql.connector.Error:
            return None

        return value or None

    def _server_version(self, connection: MySQLConnectionAbstract) -> str:
        return connection.server_info or ""

    def _connection_in_transaction(self, connection: MySQLConnectionAbstract) -> bool:
        return bool(connection.in_transaction)

    def _connect(self) -> MySQLConnectionAbstract:
        import mysql.connector

        options = self._options
        socket = self._socket
        assert socket is not None  # checked by __init__
        parameters: dict[str, str | int | None] = {
            "database": self._name,
            "user": socket.username,
            "password": socket.password,
            "charset": options.get("charset"),
            "collation": options.get("collation"),
        }

        if isinstance(socket, NetworkSocket):
            parameters |= {"host": socket.host, "port": socket.port}
        elif isinstance(socket, UnixSocket):
            parameters["unix_socket"] = socket.path

        connection = mysql.connector.connect(
            autocommit=True, **{key: value for key, value in parameters.items() if value is not None}
        )

        # Only pool arguments make connect() return a pooled connection, and none are passed.
        return cast("MySQLConnectionAbstract", connection)

    def _session_queries(self) -> list[str]:
        engine = self._options.get("engine")

        return [] if engine is None else [f"SET SESSION default_storage_engine = {engine}"]

    def _disconnect(self, connection: MySQLConnectionAbstract) -> None:
        import mysql.connector

        with contextlib.suppress(mysql.connector.Error):
            connection.close()

    def _execute(
        self, connection: MySQLConnectionAbstract, query: str, params: Sequence[Scalar] | None
    ) -> MySQLConnectorResult:
        import mysql.connector

        cursor = connection.cursor() if params is None else connection.cursor(prepared=True)

        try:
            cursor.execute(query, params or ())

            return MySQLConnectorResult(cursor)
        except mysql.connector.Error as error:
            if params is not None and error.errno == _SERVER_GONE_AWAY:
                self._raise_prepare_error(connection, query)

            raise
        finally:
            cursor.close()

    @staticmethod
    def _raise_prepare_error(connection: MySQLConnectionAbstract, query: str) -> None:
        """Raise the real error of a failed server side prepare.

        mysql-connector's C extension reports every prepare error (syntax, unknown table, ..) as 2006 "server has
        gone away". Preparing the statement again in SQL raises the server's actual error; when that succeeds, or
        the connection really is gone, nothing is raised here.
        """
        import mysql.connector

        cursor = connection.cursor()

        try:
            cursor.execute("SET @sentiencedb_statement = %s", (query,))
            cursor.execute("PREPARE sentiencedb_statement FROM @sentiencedb_statement")
            cursor.execute("DEALLOCATE PREPARE sentiencedb_statement")
        except mysql.connector.Error as error:
            if error.errno != _SERVER_GONE_AWAY:
                raise
        finally:
            cursor.close()

    def _map_error(self, error: BaseException, query: str | None, *, connecting: bool = False) -> DriverError | None:
        return map_mysql_connector_error(error, query, connecting=connecting)


__all__ = ["MySQLConnectorAdapter"]
