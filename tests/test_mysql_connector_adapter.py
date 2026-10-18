"""MySQLConnectorAdapter: connection parameters (mocked) and behaviour against a live server (skipped when
unreachable)."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from sentiencedb.dialects import MySQLDialect
from sentiencedb.driver import Driver
from sentiencedb.exceptions import AdapterError, ConnectionFailedError, ProgrammingError, UniqueViolationError
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.sockets import NetworkSocket, UnixSocket

mysql_connector = pytest.importorskip("mysql.connector")

from sentiencedb.adapters.mysql_connector_adapter import MySQLConnectorAdapter  # noqa: E402

LOCAL = NetworkSocket("127.0.0.1", 3306, "root", "")


@pytest.fixture
def fake_connect(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    connect = MagicMock()
    connect.return_value.cursor.return_value.description = None
    monkeypatch.setattr(mysql_connector, "connect", connect)

    return connect


def test_requires_a_socket() -> None:
    with pytest.raises(AdapterError):
        MySQLConnectorAdapter(Driver.MYSQL, "app", None, (), {}, None)


def test_network_socket_and_options(fake_connect: MagicMock) -> None:
    options: Any = {"charset": "utf8mb4", "collation": "utf8mb4_bin", "engine": "InnoDB"}

    MySQLConnectorAdapter(
        Driver.MYSQL, "app", NetworkSocket("db", 3307, "user", "secret"), ["SET @a = 1"], options, None
    )

    assert fake_connect.call_args.kwargs == {
        "autocommit": True,
        "database": "app",
        "host": "db",
        "port": 3307,
        "user": "user",
        "password": "secret",
        "charset": "utf8mb4",
        "collation": "utf8mb4_bin",
    }
    cursor = fake_connect.return_value.cursor.return_value
    assert [call.args for call in cursor.execute.call_args_list] == [
        ("SET SESSION default_storage_engine = InnoDB", ()),
        ("SET @a = 1", ()),
    ]


def test_unix_socket(fake_connect: MagicMock) -> None:
    MySQLConnectorAdapter(Driver.MARIADB, "app", UnixSocket("/run/mysqld/mysqld.sock", "root"), (), {}, None)

    assert fake_connect.call_args.kwargs == {
        "autocommit": True,
        "database": "app",
        "unix_socket": "/run/mysqld/mysqld.sock",
        "user": "root",
    }


def test_connect_errors_become_connection_failed(fake_connect: MagicMock) -> None:
    fake_connect.side_effect = mysql_connector.errors.InterfaceError(msg="Can't connect", errno=2003)

    with pytest.raises(ConnectionFailedError) as error:
        MySQLConnectorAdapter(Driver.MYSQL, "app", LOCAL, (), {}, None)

    assert error.value.code == 2003


def server_is_up(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
    except OSError:
        return False

    return True


@pytest.fixture(scope="module")
def database() -> str:
    if not server_is_up(3306):
        pytest.skip("MySQL is not reachable on localhost:3306")

    MySQLConnectorAdapter(Driver.MYSQL, "mysql", LOCAL, ["CREATE DATABASE IF NOT EXISTS sentiencedb"], {}, None).close()

    return "sentiencedb"


@pytest.fixture
def adapter(database: str) -> Iterator[MySQLConnectorAdapter]:
    adapter = MySQLConnectorAdapter(Driver.MYSQL, database, LOCAL, (), {}, None)
    adapter.exec(
        "CREATE TEMPORARY TABLE adapter_users (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(20) UNIQUE)"
        " ENGINE = InnoDB"
    )
    yield adapter
    adapter.close()


@pytest.fixture
def dialect(adapter: MySQLConnectorAdapter) -> MySQLDialect:
    return MySQLDialect(Driver.MYSQL, adapter.version())


@pytest.mark.integration
def test_version(dialect: MySQLDialect) -> None:
    assert dialect.version >= 50000


@pytest.mark.integration
def test_placeholders(adapter: MySQLConnectorAdapter, dialect: MySQLDialect) -> None:
    positional = QueryWithParams("SELECT ? AS a, '%s ?' AS b, CONCAT('%', ?) AS c", [1, "x"])
    named = QueryWithParams("SELECT :n AS a, :n + 1 AS b", {"n": 1})

    assert adapter.query("SELECT '%s ?' AS b").fetch_dict() == {"b": "%s ?"}
    assert adapter.query_with_params(dialect, positional).fetch_dict() == {"a": 1, "b": "%s ?", "c": "%x"}
    assert adapter.query_with_params(dialect, named).fetch_dict() == {"a": 1, "b": 2}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("value", "column_type", "expected"),
    [
        (True, "TINYINT", 1),
        (None, "TEXT", None),
        (Decimal("1.50"), "DECIMAL(5, 2)", Decimal("1.50")),
        (b"\x00\x01", "BLOB", b"\x00\x01"),
        (date(2024, 1, 2), "DATE", date(2024, 1, 2)),
        (datetime(2024, 1, 2, 3, 4, 5, 6), "DATETIME(6)", datetime(2024, 1, 2, 3, 4, 5, 6)),
    ],
)
def test_params_round_trip(
    adapter: MySQLConnectorAdapter, dialect: MySQLDialect, value: Any, column_type: str, expected: Any
) -> None:
    adapter.exec(f"CREATE TEMPORARY TABLE round_trip (value {column_type})")
    adapter.query_with_params(dialect, QueryWithParams("INSERT INTO round_trip VALUES (?)", [value]))

    assert adapter.query("SELECT value FROM round_trip").scalar() == expected

    adapter.exec("DROP TEMPORARY TABLE round_trip")


@pytest.mark.integration
def test_emulate_prepare(adapter: MySQLConnectorAdapter, dialect: MySQLDialect) -> None:
    query_with_params = QueryWithParams("SELECT ? AS a, ? AS b", ["it's 100%", True])

    assert adapter.query_with_params(dialect, query_with_params, emulate_prepare=True).fetch_dict() == {
        "a": "it's 100%",
        "b": 1,
    }


@pytest.mark.integration
def test_results_stay_readable_after_later_statements(adapter: MySQLConnectorAdapter, dialect: MySQLDialect) -> None:
    first = adapter.query("SELECT 1 AS a UNION ALL SELECT 2")
    second = adapter.query_with_params(dialect, QueryWithParams("SELECT ? AS b", [3]))

    assert first.columns() == {"a": "LONGLONG"}
    assert second.fetch_dicts() == [{"b": 3}]
    assert first.fetch_dicts() == [{"a": 1}, {"a": 2}]


@pytest.mark.integration
def test_transactions_and_savepoints(adapter: MySQLConnectorAdapter, dialect: MySQLDialect) -> None:
    adapter.begin_transaction(dialect)
    adapter.exec("INSERT INTO adapter_users (name) VALUES ('Alice')")
    adapter.begin_savepoint(dialect, "sp")
    adapter.exec("INSERT INTO adapter_users (name) VALUES ('Bob')")
    adapter.rollback_savepoint(dialect, "sp")
    assert adapter.in_transaction()
    adapter.commit_transaction(dialect)

    assert not adapter.in_transaction()
    assert adapter.query("SELECT name FROM adapter_users").fetch_dicts() == [{"name": "Alice"}]


@pytest.mark.integration
def test_last_insert_id(adapter: MySQLConnectorAdapter, dialect: MySQLDialect) -> None:
    adapter.query_with_params(dialect, QueryWithParams("INSERT INTO adapter_users (name) VALUES (?)", ["Alice"]))
    adapter.query_with_params(dialect, QueryWithParams("INSERT INTO adapter_users (name) VALUES (?)", ["Bob"]))

    assert adapter.last_insert_id() == 2


@pytest.mark.integration
def test_errors_are_mapped(adapter: MySQLConnectorAdapter, dialect: MySQLDialect) -> None:
    insert = QueryWithParams("INSERT INTO adapter_users (name) VALUES (?)", ["Alice"])
    adapter.query_with_params(dialect, insert)

    with pytest.raises(UniqueViolationError) as error:
        adapter.query_with_params(dialect, insert)

    assert (error.value.code, error.value.sqlstate, error.value.query) == (1062, "23000", insert.query)

    with pytest.raises(ProgrammingError):
        adapter.exec("SELEC 1")


@pytest.mark.integration
def test_connection_per_thread(adapter: MySQLConnectorAdapter) -> None:
    ids: list[int] = []
    thread = threading.Thread(target=lambda: ids.append(adapter.query("SELECT CONNECTION_ID()").scalar()))
    thread.start()
    thread.join()

    assert ids[0] != adapter.query("SELECT CONNECTION_ID()").scalar()
