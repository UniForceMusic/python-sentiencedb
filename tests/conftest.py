"""Shared fixtures.

Tests run with ``--import-mode=importlib``, so helpers are exposed as fixtures rather than imported from here.

* ``sql_dialect`` / ``mysql_dialect`` / ``mariadb_dialect`` / ``pgsql_dialect`` / ``sqlite_dialect``: dialects at a
  recent server version.
* ``recording_dialect``: a SQLite dialect whose statement methods record their keyword arguments in ``.calls`` and
  return ``QueryWithParams("<METHOD>")``; lets builder tests assert exactly what a builder hands to its dialect.
* ``mock_database``: an autospec'd :class:`DatabaseABC`.
* ``static_query``: ``static_query(sql, params=())`` builds a :class:`Query` that compiles to the given SQL; use it as
  a sub-select in dialect tests without depending on ``SelectQuery``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from unittest.mock import create_autospec

import pytest

from sentiencedb.database_abc import DatabaseABC
from sentiencedb.dialects import MySQLDialect, PgSQLDialect, SQLDialect, SQLiteDialect
from sentiencedb.driver import Driver
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.queries.query import Query
from sentiencedb.queries.type_aliases import Scalar


class RecordingDialect(SQLiteDialect):
    """Records the keyword arguments of every statement call instead of generating SQL."""

    def __init__(self) -> None:
        super().__init__(Driver.SQLITE, "3.45.1")
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def last_call(self) -> tuple[str, dict[str, Any]]:
        return self.calls[-1]

    def _record(self, method: str, kwargs: dict[str, Any]) -> QueryWithParams:
        self.calls.append((method, kwargs))

        return QueryWithParams(method.upper())

    def select(self, **kwargs: Any) -> QueryWithParams:
        return self._record("select", kwargs)

    def insert(self, **kwargs: Any) -> QueryWithParams:
        return self._record("insert", kwargs)

    def update(self, **kwargs: Any) -> QueryWithParams:
        return self._record("update", kwargs)

    def delete(self, **kwargs: Any) -> QueryWithParams:
        return self._record("delete", kwargs)

    def create_table(self, **kwargs: Any) -> QueryWithParams:
        return self._record("create_table", kwargs)

    def alter_table(self, **kwargs: Any) -> list[QueryWithParams]:
        return [self._record("alter_table", kwargs)]

    def drop_table(self, **kwargs: Any) -> QueryWithParams:
        return self._record("drop_table", kwargs)

    def create_index(self, **kwargs: Any) -> QueryWithParams:
        return self._record("create_index", kwargs)

    def drop_index(self, **kwargs: Any) -> QueryWithParams:
        return self._record("drop_index", kwargs)


class StaticQuery(Query):
    """A query that always compiles to the same SQL."""

    def __init__(self, sql: str, params: Sequence[Scalar] = ()) -> None:
        super().__init__(create_autospec(DatabaseABC, instance=True), SQLiteDialect(Driver.SQLITE, "3.45.1"))
        self._query_with_params = QueryWithParams(sql, list(params))

    def to_query_with_params(self) -> QueryWithParams:
        return self._query_with_params


@pytest.fixture
def sql_dialect() -> SQLDialect:
    return SQLDialect(Driver.SQLITE, "3.45.1")


@pytest.fixture
def mysql_dialect() -> MySQLDialect:
    return MySQLDialect(Driver.MYSQL, "8.4.3")


@pytest.fixture
def mariadb_dialect() -> MySQLDialect:
    return MySQLDialect(Driver.MARIADB, "11.4.4-MariaDB")


@pytest.fixture
def pgsql_dialect() -> PgSQLDialect:
    return PgSQLDialect(Driver.PGSQL, "18.0")


@pytest.fixture
def sqlite_dialect() -> SQLiteDialect:
    return SQLiteDialect(Driver.SQLITE, "3.45.1")


@pytest.fixture
def recording_dialect() -> RecordingDialect:
    return RecordingDialect()


@pytest.fixture
def mock_database() -> Any:
    return create_autospec(DatabaseABC, instance=True)


@pytest.fixture
def static_query() -> Callable[..., Query]:
    return StaticQuery
