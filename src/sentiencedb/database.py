"""The :class:`Database` facade."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Self, TypeVar

from .database_abc import DatabaseABC
from .exceptions import AdapterError
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

if TYPE_CHECKING:
    from .adapters.adapter_abc import AdapterABC
    from .dialects.dialect_abc import DialectABC
    from .queries.objects.ddl import Column, ForeignKeyConstraint, Index, UniqueConstraint
    from .queries.type_aliases import IdentifierLike, Scalar, TableRef
    from .results.result_abc import ResultABC
    from .schemas.schema_abc import SchemaABC

T = TypeVar("T")


class _ThreadState(threading.local):
    """The calling thread's stack of open savepoint names and :meth:`Database.connection` nesting depth."""

    def __init__(self) -> None:
        self.savepoints: list[str] = []
        self.connection_depth = 0


class Database(DatabaseABC):
    """The database facade: raw SQL, transactions, query builders and introspection on one connection target.

    Create it with :func:`~sentiencedb.connect` or one of the ``connect_*`` functions. It is safe to share between
    threads (see :class:`AdapterABC` for the connection model); transactions and their savepoints belong to the calling
    thread.

    :param adapter: Executes the SQL.
    :param dialect: Generates the SQL; must match the adapter's server.
    :param schema: Answers the ``information_schema_*`` methods.
    """

    def __init__(self, adapter: AdapterABC, dialect: DialectABC, schema: SchemaABC) -> None:
        self._adapter = adapter
        self._dialect = dialect
        self._schema = schema
        self._thread = _ThreadState()

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
        savepoints = self._thread.savepoints

        if not self._adapter.in_transaction():
            savepoints.clear()
            self._adapter.begin_transaction(self._dialect)
            return

        if not self._dialect.savepoints:
            raise AdapterError("cannot nest transactions: the dialect has no savepoints")

        savepoint = name or f"savepoint_{len(savepoints) + 1}"
        self._adapter.begin_savepoint(self._dialect, savepoint)
        savepoints.append(savepoint)

    def commit_transaction(self, release_savepoints: bool = False, name: str | None = None) -> None:
        if not self._adapter.in_transaction():
            return

        savepoints = self._thread.savepoints

        if release_savepoints or not savepoints:
            savepoints.clear()
            self._adapter.commit_transaction(self._dialect)
            return

        self._adapter.commit_savepoint(self._dialect, self._pop_savepoint(name))

    def rollback_transaction(self, release_savepoints: bool = False, name: str | None = None) -> None:
        if not self._adapter.in_transaction():
            return

        savepoints = self._thread.savepoints

        if release_savepoints or not savepoints:
            savepoints.clear()
            self._adapter.rollback_transaction(self._dialect)
            return

        self._adapter.rollback_savepoint(self._dialect, self._pop_savepoint(name))

    def _pop_savepoint(self, name: str | None) -> str:
        """Remove the savepoint ``name`` (default the innermost) and every later one from the stack."""
        savepoints = self._thread.savepoints

        if name is None:
            return savepoints.pop()

        if name not in savepoints:
            raise AdapterError(f"no open savepoint named {name!r}")

        index = len(savepoints) - 1 - savepoints[::-1].index(name)
        del savepoints[index:]

        return name

    def in_transaction(self) -> bool:
        return self._adapter.in_transaction()

    def transaction(
        self,
        callback: Callable[[Self], T],
        release_savepoints: bool = False,
        name: str | None = None,
    ) -> T:
        outer = not self._adapter.in_transaction()
        self.begin_transaction(name)
        # The level this call owns: the whole transaction, or the savepoint it just pushed.
        level = 0 if outer else len(self._thread.savepoints) - 1

        try:
            result = callback(self)
        except BaseException as error:
            self._abandon(outer, level, release_savepoints, error)
            raise

        try:
            self._end(outer, level, release_savepoints, commit=True)
        except BaseException as error:
            self._abandon(outer, level, release_savepoints, error)
            raise

        return result

    def _end(self, outer: bool, level: int, release_savepoints: bool, *, commit: bool) -> None:
        """Commit or roll back the level a :meth:`transaction` call opened, whatever the callback left open."""
        savepoints = self._thread.savepoints

        if not self._adapter.in_transaction():
            savepoints.clear()  # the callback (or the server) ended the transaction
            return

        if outer or release_savepoints:
            savepoints.clear()

            if commit:
                self._adapter.commit_transaction(self._dialect)
            else:
                self._adapter.rollback_transaction(self._dialect)

            return

        if len(savepoints) <= level:
            return  # the callback already ended this savepoint

        savepoint = savepoints[level]

        if commit:
            # A failed release leaves the savepoint on the stack, so _abandon can roll back to it.
            self._adapter.commit_savepoint(self._dialect, savepoint)
            del savepoints[level:]
        else:
            del savepoints[level:]
            self._adapter.rollback_savepoint(self._dialect, savepoint)

    def _abandon(self, outer: bool, level: int, release_savepoints: bool, error: BaseException) -> None:
        """Roll back after ``error`` without letting a failing rollback replace it."""
        try:
            self._end(outer, level, release_savepoints, commit=False)
        except Exception as rollback_error:
            error.add_note(f"rolling back the transaction failed as well: {rollback_error!r}")

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

    def information_schema_tables(self, schema: str | None = None) -> list[str]:
        return self._schema.tables(self, self._dialect, schema)

    def information_schema_table_exists(self, table: str, schema: str | None = None) -> bool:
        return self._schema.table_exists(self, self._dialect, table, schema)

    def information_schema_index_exists(self, table: str, name: str, schema: str | None = None) -> bool:
        return self._schema.index_exists(self, self._dialect, table, name, schema)

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

    # -- connections --------------------------------------------------------------------------------------------

    def release_connection(self) -> None:
        self._adapter.release_connection()

    @contextmanager
    def connection(self) -> Iterator[Self]:
        state = self._thread
        state.connection_depth += 1

        try:
            yield self
        finally:
            state.connection_depth -= 1

            if state.connection_depth == 0:
                self._adapter.release_connection(if_idle=True)

    def close(self) -> None:
        self._adapter.close()


__all__ = ["Database"]
