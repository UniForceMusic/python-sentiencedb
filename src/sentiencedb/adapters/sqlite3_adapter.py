"""SQLite adapter on the standard library ``sqlite3`` module."""

from __future__ import annotations

import contextlib
import re
import sqlite3
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..results.sqlite3_result import SQLite3Result
from ._dbapi_adapter import DBAPIAdapter
from ._errors import map_sqlite3_error

if TYPE_CHECKING:
    from ..dialects.dialect_abc import DialectABC
    from ..driver import Driver
    from ..exceptions import DriverError
    from ..options import DebugCallback, Options
    from ..queries.type_aliases import Scalar
    from ..sockets.socket_abc import SocketABC

_REGEX_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


class SQLite3Adapter(DBAPIAdapter[sqlite3.Connection]):
    """SQLite adapter on the standard library ``sqlite3`` module.

    Connections run in autocommit mode; transactions are explicit ``BEGIN`` statements. Every connection gets the
    SQL functions ``REGEXP`` (backing the ``REGEXP`` operator) and ``regexp_like(value, pattern, flags = '')``
    (``re.search`` with the flags ``i``, ``m``, ``s`` and ``x``) unless ``create_functions`` overrides them.

    In-memory databases (``":memory:"``, ``""``, ``file:`` URIs with ``mode=memory``) exist per connection, so
    instead of one connection per thread they use a single connection that every thread shares, one statement at
    a time. A transaction holds that connection for its thread until it ends: the other threads' statements wait
    for it rather than run inside it.

    Rows are read completely when the statement runs, so an unread result never holds a lock on the database file.
    Datetime parameters are stored as text at second precision (``%Y-%m-%d %H:%M:%S``), aware ones in UTC.

    The socket is ignored. Options: ``read_only``, ``encryption_key`` (SQLCipher builds), ``busy_timeout``,
    ``encoding``, ``journal_mode``, ``foreign_keys``, ``deprecated_case_sensitive_like``, ``create_functions`` and
    ``optimize``.
    """

    def __init__(
        self,
        driver: Driver,
        name: str,
        socket: SocketABC | None,
        queries: Sequence[str],
        options: Options,
        debug: DebugCallback | None,
    ) -> None:
        super().__init__(driver, name, socket, queries, options, debug, shared=_is_in_memory(name))

    def last_insert_id(self, name: str | None = None) -> int | str | None:
        """``last_insert_rowid()``; ``None`` instead of ``0`` when the connection has not inserted a row yet."""
        connection = self._connection()

        with self._lock:
            try:
                value: int | None = self._execute(connection, "SELECT last_insert_rowid()", None).scalar()
            except sqlite3.Error:
                return None

        return value or None

    def _server_version(self, connection: sqlite3.Connection) -> str:
        return sqlite3.sqlite_version

    def _connection_in_transaction(self, connection: sqlite3.Connection) -> bool:
        return connection.in_transaction

    def _connect(self) -> sqlite3.Connection:
        options = self._options
        database = self._name
        uri = database.startswith("file:")

        if options.get("read_only"):
            database = _read_only_uri(database)
            uri = True

        connection = sqlite3.connect(
            database,
            timeout=options.get("busy_timeout", 5000) / 1000,
            isolation_level=None,
            check_same_thread=False,
            uri=uri,
        )

        try:
            if "encryption_key" in options:
                connection.execute(f"PRAGMA key = {_quote(options['encryption_key'])}")

            functions: dict[str, Callable[..., Any]] = {
                "REGEXP": _regexp,
                "regexp_like": _regexp_like,
                **options.get("create_functions", {}),
            }

            for function_name, function in functions.items():
                connection.create_function(function_name, -1, function)
        except BaseException:
            connection.close()
            raise

        return connection

    def _session_queries(self) -> list[str]:
        options = self._options
        queries = []

        if "encoding" in options:
            queries.append(f"PRAGMA encoding = {_quote(options['encoding'])}")

        if "journal_mode" in options:
            queries.append(f"PRAGMA journal_mode = {_quote(options['journal_mode'])}")

        if options.get("foreign_keys"):
            queries.append("PRAGMA foreign_keys = ON")

        if options.get("deprecated_case_sensitive_like"):
            queries.append("PRAGMA case_sensitive_like = ON")

        return queries

    def _disconnect(self, connection: sqlite3.Connection) -> None:
        if self._options.get("optimize"):
            with contextlib.suppress(sqlite3.Error):
                connection.execute("PRAGMA optimize")

        with contextlib.suppress(sqlite3.Error):
            connection.close()

    def _execute(self, connection: sqlite3.Connection, query: str, params: Sequence[Scalar] | None) -> SQLite3Result:
        cursor = connection.execute(query) if params is None else connection.execute(query, params)

        try:
            return SQLite3Result(cursor)
        finally:
            cursor.close()

    def _cast(self, dialect: DialectABC, value: Scalar) -> Scalar:
        """Decimals are bound as text: ``sqlite3`` cannot bind them, and text keeps them exact."""
        value = dialect.cast_to_driver(value)

        return str(value) if isinstance(value, Decimal) else value

    def _map_error(self, error: BaseException, query: str | None, *, connecting: bool = False) -> DriverError | None:
        return map_sqlite3_error(error, query, connecting=connecting)


def _is_in_memory(name: str) -> bool:
    return (
        name in ("", ":memory:")
        or name.startswith("file::memory:")
        or (name.startswith("file:") and "mode=memory" in name)
    )


def _read_only_uri(name: str) -> str:
    if not name.startswith("file:"):
        return f"file:{quote(name)}?mode=ro"

    return f"{name}{'&' if '?' in name else '?'}mode=ro"


def _quote(value: str) -> str:
    """A SQLite string literal (PRAGMA values are not bindable)."""
    return "'" + value.replace("'", "''") + "'"


def _regexp(pattern: str | None, value: object) -> bool | None:
    """``value REGEXP pattern``; SQLite passes the pattern first."""
    return _regexp_like(value, pattern)


def _regexp_like(value: object, pattern: str | None, flags: str | None = "") -> bool | None:
    if value is None or pattern is None:
        return None

    re_flags = re.NOFLAG

    for flag in flags or "":
        re_flags |= _REGEX_FLAGS.get(flag, re.NOFLAG)

    return re.search(pattern, str(value), re_flags) is not None


__all__ = ["SQLite3Adapter"]
