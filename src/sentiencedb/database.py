"""The :class:`Database` facade."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Self, TypeVar

from .database_abc import DatabaseABC
from .driver import Driver
from .queries.alter_table_query import AlterTableQuery
from .queries.create_index_query import CreateIndexQuery
from .queries.create_table_query import CreateTableQuery
from .queries.delete_query import DeleteQuery
from .queries.drop_index_query import DropIndexQuery
from .queries.drop_table_query import DropTableQuery
from .queries.insert_query import InsertQuery
from .queries.objects.alias import Alias, SubQuery
from .queries.objects.query_with_params import QueryWithParams
from .queries.query_factory import QueryFactory
from .queries.select_query import SelectQuery
from .queries.update_query import UpdateQuery
from .sockets.network_socket import NetworkSocket
from .sockets.unix_socket import UnixSocket

if TYPE_CHECKING:
    from .adapters.adapter_abc import AdapterABC
    from .dialects.dialect_abc import DialectABC
    from .options import DebugCallback, Options
    from .queries.objects.ddl import Column, ForeignKeyConstraint, Index, UniqueConstraint
    from .queries.type_aliases import IdentifierLike, Scalar, TableRef
    from .results.result_abc import ResultABC
    from .schemas.schema_abc import SchemaABC
    from .sockets.socket_abc import SocketABC

T = TypeVar("T")


class _Savepoints(threading.local):
    """The calling thread's stack of open savepoint names."""

    def __init__(self) -> None:
        self.names: list[str] = []


class Database(DatabaseABC):
    """The database facade: raw SQL, transactions, query builders and introspection on one connection target.

    Create it with :meth:`connect` or one of the ``connect_*`` helpers. It is safe to share between threads (see
    :class:`AdapterABC` for the connection model); transactions and their savepoints belong to the calling thread.

    :param adapter: Executes the SQL.
    :param dialect: Generates the SQL; must match the adapter's server.
    :param schema: Answers the ``information_schema_*`` methods.
    """

    def __init__(self, adapter: AdapterABC, dialect: DialectABC, schema: SchemaABC) -> None:
        self._adapter = adapter
        self._dialect = dialect
        self._schema = schema
        self._savepoints = _Savepoints()

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

        return cls(adapter, driver.dialect(adapter.version(), options), driver.schema())

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
        return cls.connect(Driver.SQLITE, name, None, queries, options, debug)

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
        socket = _socket(host, port, username, password, unix_socket)

        return cls.connect(Driver.PGSQL, name, socket, queries, options, debug)

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
        socket = _socket(host, port, username, password, unix_socket)

        return cls.connect(Driver.MYSQL, name, socket, queries, options, debug)

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
        socket = _socket(host, port, username, password, unix_socket)

        return cls.connect(Driver.MARIADB, name, socket, queries, options, debug)

    @property
    def dialect(self) -> DialectABC:
        return self._dialect

    # -- raw execution ------------------------------------------------------------------------------------------

    def exec(self, query: str) -> None:
        self._adapter.exec(query)

    def query(self, query: str) -> ResultABC:
        return self._adapter.query(query)

    def prepared(
        self,
        query: str,
        params: Sequence[Scalar] | Mapping[str, Scalar] = (),
        emulate_prepare: bool = False,
    ) -> ResultABC:
        return self.query_with_params(QueryWithParams(query, params), emulate_prepare)

    def query_with_params(self, query_with_params: QueryWithParams, emulate_prepare: bool = False) -> ResultABC:
        if not query_with_params.params:
            return self._adapter.query(query_with_params.query)

        return self._adapter.query_with_params(self._dialect, query_with_params, emulate_prepare)

    # -- transactions -------------------------------------------------------------------------------------------

    def begin_transaction(self, name: str | None = None) -> None:
        savepoints = self._savepoints.names

        if not self._adapter.in_transaction():
            savepoints.clear()
            self._adapter.begin_transaction(self._dialect)
            return

        savepoint = name or f"savepoint_{len(savepoints) + 1}"
        savepoints.append(savepoint)
        self._adapter.begin_savepoint(self._dialect, savepoint)

    def commit_transaction(self, release_savepoints: bool = False, name: str | None = None) -> None:
        if not self._adapter.in_transaction():
            return

        savepoints = self._savepoints.names

        if release_savepoints or not savepoints:
            savepoints.clear()
            self._adapter.commit_transaction(self._dialect)
            return

        self._adapter.commit_savepoint(self._dialect, savepoints.pop())

    def rollback_transaction(self, release_savepoints: bool = False, name: str | None = None) -> None:
        if not self._adapter.in_transaction():
            return

        savepoints = self._savepoints.names

        if release_savepoints or not savepoints:
            savepoints.clear()
            self._adapter.rollback_transaction(self._dialect)
            return

        self._adapter.rollback_savepoint(self._dialect, savepoints.pop())

    def in_transaction(self) -> bool:
        return self._adapter.in_transaction()

    def transaction(
        self,
        callback: Callable[[Self], T],
        release_savepoints: bool = False,
        name: str | None = None,
    ) -> T:
        self.begin_transaction(name)

        try:
            result = callback(self)
        except BaseException:
            self.rollback_transaction(release_savepoints)
            raise

        self.commit_transaction(release_savepoints)

        return result

    def last_insert_id(self, name: str | None = None) -> int | str | None:
        return self._adapter.last_insert_id(name)

    # -- query builders -----------------------------------------------------------------------------------------

    def select(self, table: TableRef) -> SelectQuery:
        return SelectQuery(self, self._dialect, table)

    def select_table(self, table: IdentifierLike, alias: str | None = None) -> SelectQuery:
        return self.select(table if alias is None else Alias(table, alias))

    def select_sub_query(self, select_query: SelectQuery, alias: str) -> SelectQuery:
        return self.select(SubQuery(select_query, alias))

    def insert(self, table: IdentifierLike) -> InsertQuery:
        return InsertQuery(self, self._dialect, table)

    def update(self, table: IdentifierLike) -> UpdateQuery:
        return UpdateQuery(self, self._dialect, table)

    def delete(self, table: IdentifierLike) -> DeleteQuery:
        return DeleteQuery(self, self._dialect, table)

    def create_table(self, table: IdentifierLike) -> CreateTableQuery:
        return CreateTableQuery(self, self._dialect, table)

    def alter_table(self, table: IdentifierLike) -> AlterTableQuery:
        return AlterTableQuery(self, self._dialect, table)

    def drop_table(self, table: IdentifierLike) -> DropTableQuery:
        return DropTableQuery(self, self._dialect, table)

    def create_index(self, table: IdentifierLike, name: str) -> CreateIndexQuery:
        return CreateIndexQuery(self, self._dialect, table, name)

    def drop_index(self, table: IdentifierLike, name: str) -> DropIndexQuery:
        return DropIndexQuery(self, self._dialect, table, name)

    def table(self, table: IdentifierLike) -> QueryFactory:
        return QueryFactory(self, table)

    # -- introspection ------------------------------------------------------------------------------------------

    def information_schema_schemas(self) -> list[str]:
        return self._schema.schemas(self, self._dialect)

    def information_schema_tables(self) -> list[str]:
        return self._schema.tables(self, self._dialect)

    def information_schema_columns(self, table: str) -> list[Column]:
        return self._schema.columns(self, self._dialect, table)

    def information_schema_primary_keys(self, table: str) -> list[str]:
        return self._schema.primary_keys(self, self._dialect, table)

    def information_schema_unique_constraints(self, table: str) -> list[UniqueConstraint]:
        return self._schema.unique_constraints(self, self._dialect, table)

    def information_schema_foreign_key_constraints(self, table: str) -> list[ForeignKeyConstraint]:
        return self._schema.foreign_key_constraints(self, self._dialect, table)

    def information_schema_indexes(self, table: str) -> list[Index]:
        return self._schema.indexes(self, self._dialect, table)

    # -- lifecycle ----------------------------------------------------------------------------------------------

    def close(self) -> None:
        self._adapter.close()


def _socket(host: str, port: int, username: str | None, password: str | None, unix_socket: str | None) -> SocketABC:
    if unix_socket is not None:
        return UnixSocket(unix_socket, username, password)

    return NetworkSocket(host, port, username, password)


__all__ = ["Database"]
