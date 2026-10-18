"""SQLite3Adapter against real in-memory and file databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from sentiencedb.adapters import SQLite3Adapter
from sentiencedb.dialects import SQLiteDialect
from sentiencedb.driver import Driver
from sentiencedb.exceptions import AdapterError, ConnectionFailedError, OperationalError, QueryWithParamsError
from sentiencedb.options import Options
from sentiencedb.queries.objects.query_with_params import QueryWithParams

Connect = Callable[..., SQLite3Adapter]


@pytest.fixture
def connect() -> Iterator[Connect]:
    adapters: list[SQLite3Adapter] = []

    def connect(
        name: str = ":memory:",
        queries: Sequence[str] = (),
        options: Options | None = None,
        debug: Callable[[str, float, str | None], None] | None = None,
    ) -> SQLite3Adapter:
        adapter = SQLite3Adapter(Driver.SQLITE, name, None, queries, options or {}, debug)
        adapters.append(adapter)

        return adapter

    yield connect

    for adapter in adapters:
        adapter.close()


@pytest.fixture
def adapter(connect: Connect) -> SQLite3Adapter:
    adapter = connect()
    adapter.exec("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value)")

    return adapter


def test_version_is_the_sqlite_library_version(adapter: SQLite3Adapter) -> None:
    assert adapter.version() == sqlite3.sqlite_version


def test_query_returns_rows(adapter: SQLite3Adapter) -> None:
    adapter.exec("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")

    assert adapter.query("SELECT name FROM users ORDER BY id").fetch_dicts() == [{"name": "Alice"}, {"name": "Bob"}]


def test_query_sends_sql_unprocessed(adapter: SQLite3Adapter) -> None:
    assert adapter.query("SELECT '?' AS q, '%s' AS p, ':name' AS n").fetch_dict() == {"q": "?", "p": "%s", "n": ":name"}


def test_query_with_params_binds_positional_params(adapter: SQLite3Adapter, sqlite_dialect: SQLiteDialect) -> None:
    result = adapter.query_with_params(sqlite_dialect, QueryWithParams("SELECT ? AS a, '?' AS b, ? AS c", [1, "x"]))

    assert result.fetch_dict() == {"a": 1, "b": "?", "c": "x"}


def test_query_with_params_binds_named_params(adapter: SQLite3Adapter, sqlite_dialect: SQLiteDialect) -> None:
    query_with_params = QueryWithParams("SELECT :a AS a, :b AS b, :a AS c", {"a": 1, ":b": 2})

    assert adapter.query_with_params(sqlite_dialect, query_with_params).fetch_dict() == {"a": 1, "b": 2, "c": 1}


@pytest.mark.parametrize("params", [[], [1, 2]])
def test_query_with_params_rejects_a_param_count_mismatch(
    adapter: SQLite3Adapter, sqlite_dialect: SQLiteDialect, params: list[int]
) -> None:
    with pytest.raises(QueryWithParamsError):
        adapter.query_with_params(sqlite_dialect, QueryWithParams("SELECT ?", params))


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        (True, 1),
        (False, 0),
        (None, None),
        (3, 3),
        (1.5, 1.5),
        (Decimal("1.10"), "1.10"),
        ("text", "text"),
        (b"\x00\x01", b"\x00\x01"),
        (date(2024, 1, 2), "2024-01-02"),
        (datetime(2024, 1, 2, 3, 4, 5, 6), "2024-01-02 03:04:05"),
    ],
)
def test_params_are_cast_for_the_driver(
    adapter: SQLite3Adapter, sqlite_dialect: SQLiteDialect, value: Any, stored: Any
) -> None:
    adapter.query_with_params(sqlite_dialect, QueryWithParams("INSERT INTO users (value) VALUES (?)", [value]))

    assert adapter.query("SELECT value FROM users").scalar() == stored


def test_emulate_prepare_interpolates_params(connect: Connect, sqlite_dialect: SQLiteDialect) -> None:
    executed: list[str] = []
    adapter = connect(debug=lambda query, start, error: executed.append(query))
    query_with_params = QueryWithParams("SELECT ? AS a, ? AS b", ["it's", True])

    assert adapter.query_with_params(sqlite_dialect, query_with_params, emulate_prepare=True).fetch_dict() == {
        "a": "it's",
        "b": 1,
    }
    assert executed == ["SELECT 'it''s' AS a, 1 AS b"]


def test_last_insert_id(adapter: SQLite3Adapter) -> None:
    adapter.exec("INSERT INTO users (name) VALUES ('Alice')")
    adapter.exec("INSERT INTO users (name) VALUES ('Bob')")

    assert adapter.last_insert_id() == 2


def test_transaction_commit_and_rollback(adapter: SQLite3Adapter, sqlite_dialect: SQLiteDialect) -> None:
    adapter.begin_transaction(sqlite_dialect)
    adapter.exec("INSERT INTO users (name) VALUES ('Alice')")
    assert adapter.in_transaction()
    adapter.rollback_transaction(sqlite_dialect)
    assert not adapter.in_transaction()

    adapter.begin_transaction(sqlite_dialect)
    adapter.exec("INSERT INTO users (name) VALUES ('Bob')")
    adapter.commit_transaction(sqlite_dialect)

    assert adapter.query("SELECT name FROM users").fetch_dicts() == [{"name": "Bob"}]


def test_transaction_statements_follow_the_no_op_rules(connect: Connect, sqlite_dialect: SQLiteDialect) -> None:
    executed: list[str] = []
    adapter = connect(debug=lambda query, start, error: executed.append(query))

    adapter.commit_transaction(sqlite_dialect)
    adapter.rollback_transaction(sqlite_dialect)
    adapter.begin_savepoint(sqlite_dialect, "outside")
    adapter.begin_transaction(sqlite_dialect)
    adapter.begin_transaction(sqlite_dialect)
    adapter.begin_savepoint(sqlite_dialect, "inner")
    adapter.rollback_savepoint(sqlite_dialect, "inner")
    adapter.commit_savepoint(sqlite_dialect, "inner")
    adapter.commit_transaction(sqlite_dialect)

    assert executed == [
        "BEGIN TRANSACTION",
        'SAVEPOINT "inner"',
        'ROLLBACK TO "inner"',
        'RELEASE SAVEPOINT "inner"',
        "COMMIT",
    ]


def test_savepoint_rollback_keeps_earlier_work(adapter: SQLite3Adapter, sqlite_dialect: SQLiteDialect) -> None:
    adapter.begin_transaction(sqlite_dialect)
    adapter.exec("INSERT INTO users (name) VALUES ('Alice')")
    adapter.begin_savepoint(sqlite_dialect, "sp")
    adapter.exec("INSERT INTO users (name) VALUES ('Bob')")
    adapter.rollback_savepoint(sqlite_dialect, "sp")
    adapter.commit_transaction(sqlite_dialect)

    assert adapter.query("SELECT name FROM users").fetch_dicts() == [{"name": "Alice"}]


def test_debug_callback_reports_statements_and_errors(connect: Connect, sqlite_dialect: SQLiteDialect) -> None:
    calls: list[tuple[str, float, str | None]] = []
    adapter = connect(debug=lambda query, start, error: calls.append((query, start, error)))

    adapter.query_with_params(sqlite_dialect, QueryWithParams("SELECT ?", ["a"]))

    with pytest.raises(OperationalError):
        adapter.exec("SELECT * FROM missing")

    assert [(query, error) for query, _, error in calls] == [
        ("SELECT 'a'", None),
        ("SELECT * FROM missing", "no such table: missing"),
    ]
    assert all(isinstance(start, float) for _, start, _ in calls)


def test_startup_queries_run_on_connect(connect: Connect) -> None:
    adapter = connect(queries=["CREATE TABLE started (id INTEGER)", "INSERT INTO started VALUES (1)"])

    assert adapter.query("SELECT count(*) FROM started").scalar() == 1


def test_failing_startup_query_fails_the_connection(connect: Connect) -> None:
    with pytest.raises(ConnectionFailedError) as error:
        connect(queries=["SELEC 1"])

    assert error.value.query == "SELEC 1"
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)


def test_unopenable_database_fails_the_connection(connect: Connect, tmp_path: Path) -> None:
    with pytest.raises(ConnectionFailedError):
        connect(str(tmp_path / "missing" / "db.sqlite"))


def test_regexp_operator(adapter: SQLite3Adapter) -> None:
    result = adapter.query("SELECT 'abc' REGEXP '^a' AS a, 'abc' REGEXP '(?i)^A' AS b, NULL REGEXP 'a' AS c")

    assert result.fetch_dict() == {"a": 1, "b": 1, "c": None}


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ("'ABC', 'b'", 0),
        ("'ABC', 'b', 'i'", 1),
        ("'a' || char(10) || 'b', '^b$', 'm'", 1),
        ("'a' || char(10) || 'b', 'a.b', 's'", 1),
        ("'ab', 'a b', 'x'", 1),
        ("'ABC', 'b', 'iq'", 1),
        ("123, '^12'", 1),
        ("NULL, 'a'", None),
    ],
)
def test_regexp_like_function(adapter: SQLite3Adapter, arguments: str, expected: int | None) -> None:
    assert adapter.query(f"SELECT regexp_like({arguments})").scalar() == expected


def test_create_functions_add_and_override_functions(connect: Connect) -> None:
    adapter = connect(options={"create_functions": {"double": lambda value: value * 2, "regexp_like": lambda *_: 42}})

    assert adapter.query("SELECT double(21) AS a, regexp_like('a', 'b') AS b").fetch_dict() == {"a": 42, "b": 42}


def test_pragma_options(connect: Connect, tmp_path: Path) -> None:
    adapter = connect(
        str(tmp_path / "pragmas.sqlite"),
        options={
            "journal_mode": "WAL",
            "foreign_keys": True,
            "busy_timeout": 1234,
            "encoding": "UTF-16le",
            "deprecated_case_sensitive_like": True,
        },
    )

    assert adapter.query("PRAGMA journal_mode").scalar() == "wal"
    assert adapter.query("PRAGMA foreign_keys").scalar() == 1
    assert adapter.query("PRAGMA busy_timeout").scalar() == 1234
    assert adapter.query("PRAGMA encoding").scalar() == "UTF-16le"
    assert adapter.query("SELECT 'a' LIKE 'A'").scalar() == 0


def test_encryption_key_is_quoted(connect: Connect) -> None:
    executed: list[str] = []

    adapter = connect(options={"encryption_key": "it's"}, debug=lambda query, start, error: executed.append(query))

    assert adapter.query("SELECT 1").scalar() == 1
    assert executed == ["SELECT 1"]


def test_read_only(connect: Connect, tmp_path: Path) -> None:
    path = tmp_path / "read only.sqlite"
    connect(str(path)).exec("CREATE TABLE t (id INTEGER)")
    adapter = connect(str(path), options={"read_only": True})

    assert adapter.query("SELECT count(*) FROM t").scalar() == 0

    with pytest.raises(OperationalError, match="readonly"):
        adapter.exec("INSERT INTO t VALUES (1)")


def test_optimize_runs_before_close(connect: Connect, tmp_path: Path) -> None:
    executed: list[str] = []
    adapter = connect(str(tmp_path / "optimize.sqlite"), options={"optimize": True})
    adapter._connection().set_trace_callback(executed.append)

    adapter.close()

    assert executed == ["PRAGMA optimize"]


def test_closed_adapter_cannot_be_used(adapter: SQLite3Adapter) -> None:
    adapter.close()
    adapter.close()

    with pytest.raises(AdapterError):
        adapter.query("SELECT 1")
