"""PostgreSQL adapter on psycopg 3."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..exceptions import AdapterError, OperationalError
from ..results.psycopg_result import PsycopgResult
from ..sockets.network_socket import NetworkSocket
from ..sockets.unix_socket import UnixSocket
from ._dbapi_adapter import DBAPIAdapter
from ._errors import map_psycopg_error

if TYPE_CHECKING:
    from psycopg import Connection

    from ..dialects.dialect_abc import DialectABC
    from ..driver import Driver
    from ..exceptions import DriverError
    from ..options import DebugCallback, Options
    from ..queries.objects.query_with_params import QueryWithParams
    from ..queries.type_aliases import Scalar
    from ..sockets.socket_abc import SocketABC


class PsycopgAdapter(DBAPIAdapter["Connection[tuple[Any, ...]]"]):
    """PostgreSQL adapter on psycopg 3.

    Connections run in autocommit mode; transactions are explicit ``BEGIN`` statements. After an error PostgreSQL
    aborts the transaction, and a ``COMMIT`` would silently roll it back: committing an aborted transaction (or
    releasing a savepoint inside one) therefore rolls it back and raises :class:`OperationalError` instead.

    A :class:`UnixSocket` path is the socket *directory*. Options: ``ssl_mode``, ``ssl_cert``, ``ssl_key``,
    ``ssl_root_cert``, ``ssl_crl``, ``client_encoding`` and ``search_path``.
    """

    TIMEZONE_AWARE = True
    DRIVER_HINT = "PostgreSQL support needs psycopg: pip install 'sentiencedb[postgres]'"

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
            raise AdapterError("PostgreSQL requires a socket")

        super().__init__(driver, name, socket, queries, options, debug)

    def commit_transaction(self, dialect: DialectABC) -> None:
        if self._aborted():
            self.rollback_transaction(dialect)
            raise OperationalError(
                "the transaction was aborted by an earlier error and has been rolled back", sqlstate="25P02"
            )

        super().commit_transaction(dialect)

    def commit_savepoint(self, dialect: DialectABC, name: str) -> None:
        if self._aborted():
            self.rollback_savepoint(dialect, name)
            raise OperationalError(
                f"the transaction was aborted by an earlier error and has been rolled back to savepoint {name}",
                sqlstate="25P02",
            )

        super().commit_savepoint(dialect, name)

    def _aborted(self) -> bool:
        from psycopg.pq import TransactionStatus

        return self._connection().info.transaction_status == TransactionStatus.INERROR

    def _server_version(self, connection: Connection[tuple[Any, ...]]) -> str:
        return connection.info.parameter_status("server_version") or ""

    def _connection_in_transaction(self, connection: Connection[tuple[Any, ...]]) -> bool:
        from psycopg.pq import TransactionStatus

        return connection.info.transaction_status in (TransactionStatus.INTRANS, TransactionStatus.INERROR)

    def last_insert_id(self, name: str | None = None) -> int | str | None:
        """``currval(name)``, or ``lastval()`` without a name; ``None`` when the session has no such value yet."""
        import psycopg

        connection = self._connection()
        query, params = ("SELECT currval(%s::regclass)", [name]) if name else ("SELECT lastval()", None)
        # Within a transaction a savepoint keeps the error of a missing value from aborting the transaction.
        savepoint = connection.transaction() if self.in_transaction() else contextlib.nullcontext()

        try:
            with savepoint:
                value: int | str | None = self._execute(connection, query, params).scalar()
        except psycopg.Error:
            return None

        return value

    def _connect(self) -> Connection[tuple[Any, ...]]:
        import psycopg
        from psycopg.conninfo import make_conninfo

        options = self._options
        socket = self._socket
        assert socket is not None  # checked by __init__
        parameters: dict[str, str | int | None] = {
            "dbname": self._name,
            "user": socket.username,
            "password": socket.password,
            "sslmode": options.get("ssl_mode"),
            "sslcert": options.get("ssl_cert"),
            "sslkey": options.get("ssl_key"),
            "sslrootcert": options.get("ssl_root_cert"),
            "sslcrl": options.get("ssl_crl"),
            "client_encoding": options.get("client_encoding"),
        }

        if isinstance(socket, NetworkSocket):
            parameters |= {"host": socket.host, "port": socket.port}
        elif isinstance(socket, UnixSocket):
            parameters["host"] = socket.path

        return psycopg.connect(make_conninfo("", **parameters), autocommit=True)

    def _session_queries(self) -> list[str]:
        search_path = self._options.get("search_path")

        return [] if search_path is None else [f"SET search_path TO {search_path}"]

    def _disconnect(self, connection: Connection[tuple[Any, ...]]) -> None:
        import psycopg

        with contextlib.suppress(psycopg.Error):
            connection.close()

    def _execute(
        self, connection: Connection[tuple[Any, ...]], query: str, params: Sequence[Scalar] | None
    ) -> PsycopgResult:
        return PsycopgResult(connection.execute(query, params))

    def _convert_placeholders(self, query_with_params: QueryWithParams) -> QueryWithParams:
        return query_with_params.question_marks_to_percent_s()

    def _map_error(self, error: BaseException, query: str | None, *, connecting: bool = False) -> DriverError | None:
        return map_psycopg_error(error, query, connecting=connecting)


__all__ = ["PsycopgAdapter"]
