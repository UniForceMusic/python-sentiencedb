"""The adapter interface: one driver, its connections and its quirks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dialects.dialect_abc import DialectABC
    from ..driver import Driver
    from ..options import DebugCallback, Options
    from ..queries.objects.query_with_params import QueryWithParams
    from ..results.result_abc import ResultABC
    from ..sockets.socket_abc import SocketABC


class AdapterABC(ABC):
    """Executes SQL through a DB-API driver.

    Contract shared by every adapter:

    * **Connections are per thread.** Each thread lazily opens its own driver connection and keeps it until the
      thread exits or calls :meth:`release_connection`. The constructor connects eagerly so configuration errors
      surface in :meth:`Database.connect`; with ``max_connections`` that first connection is released right away so
      it does not hold a slot. ``max_connections`` caps the number of open connections and ``acquire_timeout``
      bounds the wait for a free slot. :meth:`close` closes all of them.
    * **Autocommit.** Every statement commits immediately unless a transaction was started with
      :meth:`begin_transaction`, which runs the dialect's ``BEGIN`` statement.
    * **Placeholders.** :meth:`query_with_params` receives ``?`` placeholders and converts them to the driver's
      paramstyle; every parameter goes through :meth:`DialectABC.cast_to_driver` first, after ``Enum`` members are
      replaced by their value. On engines without a time zone type (MySQL, MariaDB, SQLite) the dialect converts
      timezone-aware datetimes to UTC and binds them without offset; naive datetimes are bound as they are.
    * **Errors.** Driver exceptions are re-raised as :class:`~sentiencedb.exceptions.DriverError` subclasses with
      the original as ``__cause__``.
    * **Debugging.** When a ``debug`` callback is set it is called after every statement with the interpolated SQL,
      the start time and the error message (or ``None``). It never raises: values that have no SQL literal are
      shown as ``query -- params: [...]``, and an exception raised by the callback becomes a ``RuntimeWarning``.

    :param driver: Which engine (MySQL and MariaDB share an adapter).
    :param name: Database name, or the file path / ``":memory:"`` for SQLite.
    :param socket: Server location; required for server databases, ignored by SQLite.
    :param queries: Statements run on every new connection, after the adapter's own setup.
    :param options: Connection options.
    :param debug: Statement callback.
    :raises AdapterError: Invalid configuration (e.g. a server driver without a socket), or the driver package
        cannot be imported (a broken installation).
    :raises ConnectionFailedError: The initial connection could not be opened.
    """

    @abstractmethod
    def __init__(
        self,
        driver: Driver,
        name: str,
        socket: SocketABC | None,
        queries: Sequence[str],
        options: Options,
        debug: DebugCallback | None,
    ) -> None: ...

    @abstractmethod
    def version(self) -> str:
        """The server version string as reported by the server (``"16.2"``, ``"10.11.6-MariaDB"``, ``"3.45.1"``)."""

    @abstractmethod
    def exec(self, query: str) -> None:
        """Execute SQL without parameters, discarding any result."""

    @abstractmethod
    def query(self, query: str) -> ResultABC:
        """Execute SQL without parameters. The SQL is sent as is (no placeholder or ``%`` processing)."""

    @abstractmethod
    def query_with_params(
        self, dialect: DialectABC, query_with_params: QueryWithParams, emulate_prepare: bool = False
    ) -> ResultABC:
        """Execute SQL with parameters.

        :param emulate_prepare: Interpolate the parameters client side with :meth:`QueryWithParams.to_sql` and send
            plain SQL instead of binding them.
        :raises QueryWithParamsError: Placeholders and parameters do not match.
        """

    @abstractmethod
    def begin_transaction(self, dialect: DialectABC) -> None:
        """Start a transaction on the calling thread's connection; no-op if one is already active."""

    @abstractmethod
    def commit_transaction(self, dialect: DialectABC) -> None:
        """Commit the calling thread's transaction; no-op outside a transaction."""

    @abstractmethod
    def rollback_transaction(self, dialect: DialectABC) -> None:
        """Roll back the calling thread's transaction; no-op outside a transaction."""

    @abstractmethod
    def begin_savepoint(self, dialect: DialectABC, name: str) -> None:
        """``SAVEPOINT name``; no-op outside a transaction."""

    @abstractmethod
    def commit_savepoint(self, dialect: DialectABC, name: str) -> None:
        """``RELEASE SAVEPOINT name``; same no-op rules as :meth:`begin_savepoint`."""

    @abstractmethod
    def rollback_savepoint(self, dialect: DialectABC, name: str) -> None:
        """``ROLLBACK TO name``; same no-op rules as :meth:`begin_savepoint`."""

    @abstractmethod
    def in_transaction(self) -> bool:
        """Whether the calling thread's connection is inside a transaction."""

    @abstractmethod
    def last_insert_id(self, name: str | None = None) -> int | str | None:
        """The id generated by the calling thread's last insert.

        :param name: PostgreSQL sequence name (``currval``); without it ``lastval()`` is used. Ignored elsewhere.
        :returns: An ``int`` when numeric, ``None`` when unavailable.
        """

    @abstractmethod
    def release_connection(self, *, if_idle: bool = False) -> None:
        """Close the calling thread's connection and free its ``max_connections`` slot; the thread's next statement
        opens a new one. No-op when the thread has no connection, and for a shared connection (SQLite in-memory).

        :param if_idle: Keep a connection that is inside a transaction instead of raising.
        :raises AdapterError: The connection is inside a transaction.
        """

    @abstractmethod
    def close(self) -> None:
        """Close every connection of every thread. The adapter cannot be used afterwards (``AdapterError``)."""


__all__ = ["AdapterABC"]
