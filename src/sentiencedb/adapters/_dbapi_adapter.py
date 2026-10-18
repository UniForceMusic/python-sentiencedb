"""The part of the adapters that is the same for every PEP 249 driver."""

from __future__ import annotations

import threading
import time
from abc import abstractmethod
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Generic, TypeVar

from ..exceptions import QueryWithParamsError
from ..queries.objects.query_with_params import REGEX_PATTERN, QueryWithParams
from ._thread_local_store import ThreadLocalStore
from .adapter_abc import AdapterABC

if TYPE_CHECKING:
    from ..dialects.dialect_abc import DialectABC
    from ..driver import Driver
    from ..exceptions import DriverError
    from ..options import DebugCallback, Options
    from ..queries.type_aliases import Scalar
    from ..results.result_abc import ResultABC
    from ..sockets.socket_abc import SocketABC

ConnectionT = TypeVar("ConnectionT")


class DBAPIAdapter(AdapterABC, Generic[ConnectionT]):
    """:class:`AdapterABC` on top of a PEP 249 driver: per-thread connections, debugging, error mapping, placeholder
    validation and transactions. Subclasses supply the driver specifics through the ``_`` hooks.

    :param shared: Use one connection for all threads, serialising every statement (SQLite in-memory databases).
    """

    def __init__(
        self,
        driver: Driver,
        name: str,
        socket: SocketABC | None,
        queries: Sequence[str],
        options: Options,
        debug: DebugCallback | None,
        *,
        shared: bool = False,
    ) -> None:
        self._driver = driver
        self._name = name
        self._socket = socket
        self._queries = tuple(queries)
        self._options = options
        self._debug = debug
        self._lock: AbstractContextManager[object] = threading.RLock() if shared else nullcontext()
        self._connections = ThreadLocalStore(
            self._open,
            self._disconnect,
            shared=shared,
            max_values=options.get("max_connections"),
            acquire_timeout=options.get("acquire_timeout"),
        )
        self._connections.get()

    # -- driver hooks -------------------------------------------------------------------------------------------

    @abstractmethod
    def _connect(self) -> ConnectionT:
        """Open a driver connection with the adapter's connection options applied."""

    def _session_queries(self) -> list[str]:
        """Statements that configure a new connection; they run before the user's startup ``queries``."""
        return []

    @abstractmethod
    def _disconnect(self, connection: ConnectionT) -> None:
        """Close a connection, ignoring driver errors."""

    @abstractmethod
    def _execute(self, connection: ConnectionT, query: str, params: Sequence[Scalar] | None) -> ResultABC:
        """Run one statement; ``params`` is ``None`` for plain SQL, which the driver must send unprocessed."""

    @abstractmethod
    def _map_error(self, error: BaseException, query: str | None, *, connecting: bool = False) -> DriverError | None:
        """The package exception for a driver exception, or ``None`` when ``error`` is not one."""

    def _convert_placeholders(self, query_with_params: QueryWithParams) -> QueryWithParams:
        """Rewrite the dialect's ``?`` placeholders (or the user's ``:name`` ones) to the driver's paramstyle.

        The default keeps ``?`` and validates the parameter count.
        """
        positional = query_with_params.named_params_to_question_marks()
        placeholders = sum(match["question_mark"] is not None for match in REGEX_PATTERN.finditer(positional.query))

        if placeholders != len(positional.params):
            raise QueryWithParamsError("question mark and param count do not match")

        return positional

    def _cast(self, dialect: DialectABC, value: Scalar) -> Scalar:
        """Convert a parameter before binding it."""
        return dialect.cast_to_driver(value)

    # -- statements ---------------------------------------------------------------------------------------------

    def exec(self, query: str) -> None:
        self._run(self._connection(), query, None)

    def query(self, query: str) -> ResultABC:
        return self._run(self._connection(), query, None)

    def query_with_params(
        self, dialect: DialectABC, query_with_params: QueryWithParams, emulate_prepare: bool = False
    ) -> ResultABC:
        if emulate_prepare:
            return self.query(query_with_params.to_sql(dialect))

        converted = self._convert_placeholders(query_with_params)
        params = [self._cast(dialect, param) for param in converted.params]

        return self._run(self._connection(), converted.query, params, lambda: query_with_params.to_sql(dialect))

    # -- transactions -------------------------------------------------------------------------------------------

    def begin_transaction(self, dialect: DialectABC) -> None:
        if not self.in_transaction():
            self.exec(dialect.begin_transaction().query)

    def commit_transaction(self, dialect: DialectABC) -> None:
        if self.in_transaction():
            self.exec(dialect.commit_transaction().query)

    def rollback_transaction(self, dialect: DialectABC) -> None:
        if self.in_transaction():
            self.exec(dialect.rollback_transaction().query)

    def begin_savepoint(self, dialect: DialectABC, name: str) -> None:
        if dialect.savepoints and self.in_transaction():
            self.exec(dialect.begin_savepoint(name).query)

    def commit_savepoint(self, dialect: DialectABC, name: str) -> None:
        if dialect.savepoints and self.in_transaction():
            self.exec(dialect.commit_savepoint(name).query)

    def rollback_savepoint(self, dialect: DialectABC, name: str) -> None:
        if dialect.savepoints and self.in_transaction():
            self.exec(dialect.rollback_savepoint(name).query)

    # -- connections --------------------------------------------------------------------------------------------

    def close(self) -> None:
        self._connections.close()

    def _connection(self) -> ConnectionT:
        """The calling thread's connection, opened on first use."""
        return self._connections.get()

    def _open(self) -> ConnectionT:
        try:
            connection = self._connect()
        except Exception as error:
            mapped = self._map_error(error, None, connecting=True)

            if mapped is None:
                raise

            raise mapped from error

        try:
            for query in (*self._session_queries(), *self._queries):
                self._run(connection, query, None, connecting=True)
        except BaseException:
            self._disconnect(connection)
            raise

        return connection

    def _run(
        self,
        connection: ConnectionT,
        query: str,
        params: Sequence[Scalar] | None,
        debug_query: Callable[[], str] | None = None,
        *,
        connecting: bool = False,
    ) -> ResultABC:
        """Execute a statement, report it to the debug callback and map driver errors."""
        start = time.time()

        try:
            with self._lock:
                result = self._execute(connection, query, params)
        except Exception as error:
            self._report(debug_query, query, start, str(error))
            mapped = self._map_error(error, query, connecting=connecting)

            if mapped is None:
                raise

            raise mapped from error

        self._report(debug_query, query, start, None)

        return result

    def _report(self, debug_query: Callable[[], str] | None, query: str, start: float, error: str | None) -> None:
        if self._debug is not None:
            self._debug(query if debug_query is None else debug_query(), start, error)


__all__ = ["DBAPIAdapter"]
