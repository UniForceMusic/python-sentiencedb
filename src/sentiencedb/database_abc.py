"""The database facade interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from types import TracebackType
from typing import TYPE_CHECKING, Self, TypeVar

if TYPE_CHECKING:
    from .dialects.dialect_abc import DialectABC
    from .queries.alter_table_query import AlterTableQuery
    from .queries.create_index_query import CreateIndexQuery
    from .queries.create_table_query import CreateTableQuery
    from .queries.delete_query import DeleteQuery
    from .queries.drop_index_query import DropIndexQuery
    from .queries.drop_table_query import DropTableQuery
    from .queries.insert_query import InsertQuery
    from .queries.objects.ddl import Column, ForeignKeyConstraint, Index, UniqueConstraint
    from .queries.objects.query_with_params import QueryWithParams
    from .queries.query_factory import QueryFactory
    from .queries.select_query import SelectQuery
    from .queries.type_aliases import IdentifierLike, Scalar, TableRef
    from .queries.update_query import UpdateQuery
    from .results.result_abc import ResultABC

T = TypeVar("T")


class DatabaseABC(ABC):
    """Entry point: raw execution, transactions, query builders and introspection.

    A database is safe to share between threads; each thread uses its own connection (see :class:`AdapterABC`),
    so transactions and savepoints are per thread. Query builders and results are not thread safe.
    """

    @property
    @abstractmethod
    def dialect(self) -> DialectABC:
        """The dialect of the connected server."""

    # -- raw execution ------------------------------------------------------------------------------------------

    @abstractmethod
    def exec(self, query: str) -> None:
        """Execute SQL without parameters, discarding any result."""

    @abstractmethod
    def query(self, query: str) -> ResultABC:
        """Execute SQL without parameters."""

    @abstractmethod
    def prepared(
        self,
        query: str,
        params: Sequence[Scalar] | Mapping[str, Scalar] = (),
        emulate_prepare: bool = False,
    ) -> ResultABC:
        """Execute SQL with ``?`` placeholders and positional params, or ``:name`` placeholders and a mapping."""

    @abstractmethod
    def query_with_params(self, query_with_params: QueryWithParams, emulate_prepare: bool = False) -> ResultABC:
        """Execute a :class:`QueryWithParams`; without params it is sent as plain SQL (no placeholder processing)."""

    # -- transactions -------------------------------------------------------------------------------------------

    @abstractmethod
    def begin_transaction(self, name: str | None = None) -> None:
        """Begin a transaction, or -- when one is active -- a savepoint named ``name`` (default ``savepoint_<n>``).

        :raises AdapterError: A transaction is active and the dialect has no savepoints.
        """

    @abstractmethod
    def commit_transaction(self, release_savepoints: bool = False, name: str | None = None) -> None:
        """Release the savepoint ``name`` (default the innermost one) together with the savepoints opened after it,
        or commit when there is no savepoint (or ``release_savepoints``). No-op outside a transaction.

        :raises AdapterError: There is no open savepoint ``name``.
        :raises OperationalError: PostgreSQL: the transaction was aborted by an earlier error; it has been rolled
            back (to the savepoint) instead.
        """

    @abstractmethod
    def rollback_transaction(self, release_savepoints: bool = False, name: str | None = None) -> None:
        """Roll back to the savepoint ``name`` (default the innermost one), dropping it and the savepoints opened
        after it from the stack, or roll back the transaction when there is no savepoint (or
        ``release_savepoints``). No-op outside a transaction.

        :raises AdapterError: There is no open savepoint ``name``.
        """

    @abstractmethod
    def in_transaction(self) -> bool:
        """Whether the calling thread is inside a transaction."""

    @abstractmethod
    def transaction(
        self,
        callback: Callable[[Self], T],
        release_savepoints: bool = False,
        name: str | None = None,
    ) -> T:
        """Run ``callback(self)`` inside :meth:`begin_transaction`; commit on return, roll back and re-raise on any
        exception. Nested calls use savepoints.

        Each call ends exactly the level it opened -- the transaction, or its own savepoint -- including savepoints
        the callback left open, so the thread's connection is left as the call found it. When the commit fails the
        level is rolled back and the commit error raised. A rollback that fails as well never replaces the original
        exception; it is added to it as a note.
        """

    @abstractmethod
    def last_insert_id(self, name: str | None = None) -> int | str | None:
        """The id generated by the calling thread's last insert; see :meth:`AdapterABC.last_insert_id`."""

    # -- query builders -----------------------------------------------------------------------------------------

    @abstractmethod
    def select(self, table: TableRef) -> SelectQuery:
        """``SELECT .. FROM table``; ``table`` may be an :class:`Alias` or :class:`SubQuery`."""

    @abstractmethod
    def select_table(self, table: IdentifierLike, alias: str | None = None) -> SelectQuery:
        """``SELECT .. FROM table [AS alias]``."""

    @abstractmethod
    def select_sub_query(self, select_query: SelectQuery, alias: str) -> SelectQuery:
        """``SELECT .. FROM (select_query) AS alias``."""

    @abstractmethod
    def insert(self, table: IdentifierLike) -> InsertQuery: ...

    @abstractmethod
    def update(self, table: IdentifierLike) -> UpdateQuery: ...

    @abstractmethod
    def delete(self, table: IdentifierLike) -> DeleteQuery: ...

    @abstractmethod
    def create_table(self, table: IdentifierLike) -> CreateTableQuery: ...

    @abstractmethod
    def alter_table(self, table: IdentifierLike) -> AlterTableQuery: ...

    @abstractmethod
    def drop_table(self, table: IdentifierLike) -> DropTableQuery: ...

    @abstractmethod
    def create_index(self, table: IdentifierLike, name: str) -> CreateIndexQuery: ...

    @abstractmethod
    def drop_index(self, table: IdentifierLike, name: str) -> DropIndexQuery: ...

    @abstractmethod
    def table(self, table: IdentifierLike) -> QueryFactory:
        """Builders and introspection bound to one table (PHP ``QueryFactory``)."""

    # -- introspection ------------------------------------------------------------------------------------------

    @abstractmethod
    def information_schema_schemas(self) -> list[str]: ...

    @abstractmethod
    def information_schema_tables(self, schema: str | None = None) -> list[str]:
        """Base table names in ``schema`` (default: the current database / schema)."""

    @abstractmethod
    def information_schema_table_exists(self, table: str, schema: str | None = None) -> bool:
        """Whether the base table ``table`` exists in ``schema`` (default: the current database / schema)."""

    @abstractmethod
    def information_schema_index_exists(self, table: str, name: str, schema: str | None = None) -> bool:
        """Whether an index called ``name`` exists where it would clash with a new index on ``table``: anywhere in
        ``schema`` on PostgreSQL and SQLite, on ``table`` itself on MySQL and MariaDB."""

    @abstractmethod
    def information_schema_columns(self, table: str) -> list[Column]: ...

    @abstractmethod
    def information_schema_primary_keys(self, table: str) -> list[str]: ...

    @abstractmethod
    def information_schema_unique_constraints(self, table: str) -> list[UniqueConstraint]: ...

    @abstractmethod
    def information_schema_foreign_key_constraints(self, table: str) -> list[ForeignKeyConstraint]: ...

    @abstractmethod
    def information_schema_indexes(self, table: str) -> list[Index]: ...

    # -- connections --------------------------------------------------------------------------------------------

    @abstractmethod
    def release_connection(self) -> None:
        """Close the calling thread's connection and free its ``max_connections`` slot; the thread's next statement
        opens a new one. No-op without a connection, and for a SQLite in-memory database (its connection is shared).

        :raises AdapterError: The thread is inside a transaction.
        """

    @abstractmethod
    def connection(self) -> AbstractContextManager[Self]:
        """``with db.connection():`` -- use the calling thread's connection for the block, then release it (see
        :meth:`release_connection`) unless a transaction is still open. Nested blocks release on the outermost
        exit. Wrap the body of thread pool tasks in it so idle workers do not hold ``max_connections`` slots."""

    # -- lifecycle ----------------------------------------------------------------------------------------------

    @abstractmethod
    def close(self) -> None:
        """Close every connection. The database cannot be used afterwards."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["DatabaseABC"]
