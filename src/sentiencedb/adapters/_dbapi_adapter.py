"""The part of the adapters that is the same for every PEP 249 driver."""

from __future__ import annotations

import threading
import time
import warnings
from abc import abstractmethod
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar, cast

from ..exceptions import AdapterError, QueryWithParamsError
from ..queries.objects.query_with_params import QueryWithParams
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

    :param shared: Use one connection for all threads (SQLite in-memory databases). Statements are serialised, and a
        transaction holds the connection for its thread from ``BEGIN`` until it ends, so other threads wait for it
        instead of running inside it.
    """

    DRIVER_HINT: ClassVar[str] = ""
    """How to install the driver package; part of the error raised when it cannot be imported."""

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
        self._name = name
        self._socket = socket
        self._queries = tuple(queries)
        self._options = options
        self._debug = debug
        self._shared_lock = threading.RLock() if shared else None
        self._lock: AbstractContextManager[object] = self._shared_lock or nullcontext()
        self._transaction_owner: int | None = None
        """Shared mode: the thread whose transaction holds ``_shared_lock``."""
        self._connections = ThreadLocalStore(
            self._open,
            self._disconnect,
            shared=shared,
            max_values=options.get("max_connections"),
            acquire_timeout=options.get("acquire_timeout"),
        )
        self._version = self._server_version(self._connections.get())

        if options.get("max_connections") is not None:
            # The connection only served to validate the configuration; it must not hold a slot for good.
            self._connections.release_current()

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
    def _server_version(self, connection: ConnectionT) -> str:
        """The server version string, read once from the first connection."""

    @abstractmethod
    def _connection_in_transaction(self, connection: ConnectionT) -> bool:
        """Whether ``connection`` is inside a transaction (including a failed one)."""

    @abstractmethod
    def _execute(self, connection: ConnectionT, query: str, params: Sequence[Scalar] | None) -> ResultABC:
        """Run one statement and read its rows; ``params`` is ``None`` for plain SQL, which the driver must send
        unprocessed."""

    @abstractmethod
    def _map_error(self, error: BaseException, query: str | None, *, connecting: bool = False) -> DriverError | None:
        """The package exception for a driver exception, or ``None`` when ``error`` is not one."""

    def _convert_placeholders(self, dialect: DialectABC, query_with_params: QueryWithParams) -> QueryWithParams:
        """Rewrite the dialect's ``?`` placeholders (or the user's ``:name`` ones) to the driver's paramstyle.

        The default keeps ``?`` and validates the parameter count.
        """
        positional = query_with_params.named_params_to_question_marks(dialect)

        if positional.count_question_marks(dialect) != len(positional.params):
            raise QueryWithParamsError("question mark and param count do not match")

        return positional

    def _cast(self, dialect: DialectABC, value: Scalar) -> Scalar:
        """Convert a parameter before binding it."""
        return dialect.cast_to_driver(value)

    # -- statements ---------------------------------------------------------------------------------------------

    def version(self) -> str:
        return self._version

    def exec(self, query: str) -> None:
        self._run(self._connection(), query, None)

    def query(self, query: str) -> ResultABC:
        return self._run(self._connection(), query, None)

    def query_with_params(
        self, dialect: DialectABC, query_with_params: QueryWithParams, emulate_prepare: bool = False
    ) -> ResultABC:
        normalized = self._normalize_params(query_with_params)

        if emulate_prepare:
            return self.query(normalized.to_sql(dialect))

        converted = self._convert_placeholders(dialect, normalized)
        params = [self._cast(dialect, param) for param in converted.params]

        return self._run(self._connection(), converted.query, params, lambda: _debug_text(normalized, dialect))

    def _normalize_params(self, query_with_params: QueryWithParams) -> QueryWithParams:
        params = query_with_params.params

        if isinstance(params, Mapping):
            return QueryWithParams(query_with_params.query, {key: self._normalize(v) for key, v in params.items()})

        return QueryWithParams(query_with_params.query, [self._normalize(value) for value in params])

    def _normalize(self, param: Scalar) -> Scalar:
        """Enum members become their value."""
        value: object = param

        if isinstance(value, Enum):  # not a Scalar to the type checker, but easy to pass by accident
            value = value.value

        return cast("Scalar", value)

    # -- transactions -------------------------------------------------------------------------------------------

    def in_transaction(self) -> bool:
        if not self._connection_in_transaction(self._connection()):
            return False

        # Shared connection: the transaction belongs to the thread that began it. (``None``: a raw ``BEGIN``.)
        return self._shared_lock is None or self._transaction_owner in (None, threading.get_ident())

    def begin_transaction(self, dialect: DialectABC) -> None:
        if self.in_transaction():
            return

        if self._shared_lock is not None and self._transaction_owner != threading.get_ident():
            # Held until the transaction ends (see _run); other threads wait instead of joining the transaction.
            self._shared_lock.acquire()
            self._transaction_owner = threading.get_ident()

        self.exec(dialect.begin_transaction().query)

    def commit_transaction(self, dialect: DialectABC) -> None:
        if self.in_transaction():
            self.exec(dialect.commit_transaction().query)

    def rollback_transaction(self, dialect: DialectABC) -> None:
        if self.in_transaction():
            self.exec(dialect.rollback_transaction().query)

    def begin_savepoint(self, dialect: DialectABC, name: str) -> None:
        if self.in_transaction():
            self.exec(dialect.begin_savepoint(name).query)

    def commit_savepoint(self, dialect: DialectABC, name: str) -> None:
        if self.in_transaction():
            self.exec(dialect.commit_savepoint(name).query)

    def rollback_savepoint(self, dialect: DialectABC, name: str) -> None:
        if self.in_transaction():
            self.exec(dialect.rollback_savepoint(name).query)

    # -- connections --------------------------------------------------------------------------------------------

    def release_connection(self, *, if_idle: bool = False) -> None:
        connection = self._connections.current()

        if connection is None or self._shared_lock is not None:
            return  # nothing to release, or the shared connection, which is never released

        if self._connection_in_transaction(connection):
            if if_idle:
                return

            raise AdapterError("cannot release a connection inside a transaction")

        self._connections.release_current()

    def close(self) -> None:
        self._connections.close()

    def _connection(self) -> ConnectionT:
        """The calling thread's connection, opened on first use."""
        return self._connections.get()

    def _open(self) -> ConnectionT:
        try:
            connection = self._connect()
        except ImportError as error:
            raise AdapterError(f"{self.DRIVER_HINT} ({error})" if self.DRIVER_HINT else str(error)) from error
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
            self._report(debug_query, query, params, start, str(error))
            mapped = self._map_error(error, query, connecting=connecting)

            if mapped is None:
                raise

            raise mapped from error
        finally:
            self._release_ended_transaction(connection)

        self._report(debug_query, query, params, start, None)

        return result

    def _release_ended_transaction(self, connection: ConnectionT) -> None:
        """Shared mode: give the connection back to the other threads once the caller's transaction has ended (by
        ``COMMIT``/``ROLLBACK`` or implicitly)."""
        if self._shared_lock is None or self._transaction_owner != threading.get_ident():
            return

        try:
            ended = not self._connection_in_transaction(connection)
        except Exception:
            ended = True  # the connection was closed

        if ended:
            self._transaction_owner = None
            self._shared_lock.release()

    def _report(
        self,
        debug_query: Callable[[], str] | None,
        query: str,
        params: Sequence[Scalar] | None,
        start: float,
        error: str | None,
    ) -> None:
        """Call the debug callback. Never raises: the statement has run (and may have committed) already, and on
        failure the driver error must reach the caller."""
        if self._debug is None:
            return

        try:
            text = query if debug_query is None else debug_query()
        except Exception:  # safety net; _debug_text does not raise
            text = f"{query} -- params: {params!r}"

        try:
            self._debug(text, start, error)
        except Exception as callback_error:
            warnings.warn(f"the debug callback raised {callback_error!r}", RuntimeWarning, stacklevel=2)


def _debug_text(query_with_params: QueryWithParams, dialect: DialectABC) -> str:
    """The SQL with its parameters interpolated, or -- for values without a SQL literal (``inf``, ``nan``, driver
    specific types) -- the SQL followed by the parameters."""
    try:
        return query_with_params.to_sql(dialect)
    except Exception:
        params = query_with_params.params
        shown = dict(params) if isinstance(params, Mapping) else list(params)

        return f"{query_with_params.query} -- params: {shown!r}"


__all__ = ["DBAPIAdapter"]
