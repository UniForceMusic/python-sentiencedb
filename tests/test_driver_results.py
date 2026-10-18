"""The driver result classes: column types, single pass fetching, statements without a result set."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from sentiencedb.results import MySQLConnectorResult, PsycopgResult, Result, SQLite3Result


@pytest.fixture
def connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE t (i INTEGER, f REAL, s TEXT, b BLOB, n)")
    connection.execute("INSERT INTO t VALUES (1, 1.5, 'a', x'00', NULL), (2, NULL, 'b', NULL, 3)")

    return connection


def test_sqlite3_columns_are_the_storage_classes_of_the_first_row(connection: sqlite3.Connection) -> None:
    result = SQLite3Result(connection.execute("SELECT * FROM t ORDER BY i"))

    assert result.columns() == {"i": "INTEGER", "f": "FLOAT", "s": "TEXT", "b": "BLOB", "n": "NULL"}
    assert result.fetch_dict() == {"i": 1, "f": 1.5, "s": "a", "b": b"\x00", "n": None}
    assert result.columns() == {"i": "INTEGER", "f": "FLOAT", "s": "TEXT", "b": "BLOB", "n": "NULL"}
    assert result.fetch_dicts() == [{"i": 2, "f": None, "s": "b", "b": None, "n": 3}]
    assert result.fetch_dict() is None


def test_sqlite3_columns_after_fetching_still_describe_the_first_row(connection: sqlite3.Connection) -> None:
    result = SQLite3Result(connection.execute("SELECT f FROM t ORDER BY i"))

    assert [row["f"] for row in result] == [1.5, None]
    assert result.columns() == {"f": "FLOAT"}


def test_sqlite3_columns_without_rows(connection: sqlite3.Connection) -> None:
    result = SQLite3Result(connection.execute("SELECT i, s FROM t WHERE 0"))

    assert result.columns() == {"i": None, "s": None}
    assert result.fetch_dicts() == []


def test_sqlite3_statement_without_result_set(connection: sqlite3.Connection) -> None:
    result = SQLite3Result(connection.execute("UPDATE t SET s = 'c'"))

    assert result.columns() == {}
    assert result.fetch_dict() is None
    assert result.fetch_dicts() == []


def test_sqlite3_result_helpers(connection: sqlite3.Connection) -> None:
    assert SQLite3Result(connection.execute("SELECT s, i FROM t ORDER BY i")).scalar() == "a"
    assert SQLite3Result(connection.execute("SELECT s, i FROM t ORDER BY i")).scalar("i") == 1
    assert SQLite3Result(connection.execute("SELECT s FROM t ORDER BY i")).fetch_objects()[1].s == "b"


def test_snapshot_of_a_driver_result(connection: sqlite3.Connection) -> None:
    snapshot = Result.from_result(SQLite3Result(connection.execute("SELECT i FROM t ORDER BY i")))

    assert snapshot.columns() == {"i": "INTEGER"}
    assert snapshot.fetch_dicts() == [{"i": 1}, {"i": 2}]


class FakePsycopgCursor:
    """The parts of a psycopg cursor that :class:`PsycopgResult` reads."""

    def __init__(self, description: list[tuple[str, int]] | None, rows: list[tuple[Any, ...]]) -> None:
        type_names = {23: "int4", 25: "text"}
        self.description = (
            None if description is None else [SimpleNamespace(name=n, type_code=t) for n, t in description]
        )
        self.adapters = SimpleNamespace(
            types=SimpleNamespace(get=lambda oid: SimpleNamespace(name=type_names[oid]) if oid in type_names else None)
        )
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows.pop(0) if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows, self._rows = self._rows, []
        return rows


def test_psycopg_result() -> None:
    cursor = FakePsycopgCursor([("id", 23), ("name", 25), ("mood", 99999)], [(1, "a", "happy"), (2, "b", None)])
    result = PsycopgResult(cursor)  # type: ignore[arg-type]

    assert result.columns() == {"id": "INT4", "name": "TEXT", "mood": None}
    assert result.fetch_dict() == {"id": 1, "name": "a", "mood": "happy"}
    assert result.fetch_dicts() == [{"id": 2, "name": "b", "mood": None}]
    assert result.fetch_dict() is None


def test_psycopg_result_without_result_set() -> None:
    result = PsycopgResult(FakePsycopgCursor(None, []))  # type: ignore[arg-type]

    assert result.columns() == {}
    assert result.fetch_dict() is None
    assert result.fetch_dicts() == []


class FakeMySQLCursor:
    """The parts of a mysql-connector cursor that :class:`MySQLConnectorResult` reads."""

    def __init__(self, description: list[tuple[Any, ...]] | None, rows: list[tuple[Any, ...]]) -> None:
        self.description = description
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        if self.description is None:
            raise AssertionError("no result set to fetch")

        return self._rows


def test_mysql_connector_result_reads_every_row_up_front() -> None:
    pytest.importorskip("mysql.connector")
    cursor = FakeMySQLCursor([("id", 3), ("name", 253), ("odd", 999)], [(1, "a", None), (2, "b", None)])
    result = MySQLConnectorResult(cursor)  # type: ignore[arg-type]
    cursor._rows = []

    assert result.columns() == {"id": "LONG", "name": "VAR_STRING", "odd": None}
    assert result.fetch_dict() == {"id": 1, "name": "a", "odd": None}
    assert result.fetch_dicts() == [{"id": 2, "name": "b", "odd": None}]
    assert result.fetch_dict() is None


def test_mysql_connector_result_without_result_set() -> None:
    pytest.importorskip("mysql.connector")
    result = MySQLConnectorResult(FakeMySQLCursor(None, []))  # type: ignore[arg-type]

    assert result.columns() == {}
    assert result.fetch_dict() is None
