"""PsycopgAdapter: connection parameters (mocked) and behaviour against a live server (skipped when unreachable)."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from sentiencedb.dialects import PgSQLDialect
from sentiencedb.driver import Driver
from sentiencedb.exceptions import AdapterError, ConnectionFailedError, ProgrammingError, UniqueViolationError
from sentiencedb.options import Options
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.sockets import NetworkSocket, UnixSocket

psycopg = pytest.importorskip("psycopg")

from sentiencedb.adapters.psycopg_adapter import PsycopgAdapter  # noqa: E402

LOCAL = NetworkSocket("localhost", 5432, "postgres")


@pytest.fixture
def fake_connect(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    connect = MagicMock()
    monkeypatch.setattr(psycopg, "connect", connect)

    return connect


def conninfo(connect: MagicMock) -> dict[str, Any]:
    return dict(psycopg.conninfo.conninfo_to_dict(connect.call_args.args[0]))


def test_requires_a_socket() -> None:
    with pytest.raises(AdapterError):
        PsycopgAdapter(Driver.PGSQL, "app", None, (), {}, None)


def test_network_socket_and_options(fake_connect: MagicMock) -> None:
    options: Options = {
        "ssl_mode": "require",
        "ssl_cert": "cert.pem",
        "ssl_key": "key.pem",
        "ssl_root_cert": "root.pem",
        "ssl_crl": "crl.pem",
        "client_encoding": "UTF8",
    }

    PsycopgAdapter(Driver.PGSQL, "app", NetworkSocket("db", 6543, "user", "secret"), (), options, None)

    assert conninfo(fake_connect) == {
        "dbname": "app",
        "host": "db",
        "port": "6543",
        "user": "user",
        "password": "secret",
        "sslmode": "require",
        "sslcert": "cert.pem",
        "sslkey": "key.pem",
        "sslrootcert": "root.pem",
        "sslcrl": "crl.pem",
        "client_encoding": "UTF8",
    }
    assert fake_connect.call_args.kwargs == {"autocommit": True}


def test_unix_socket_is_the_host_directory(fake_connect: MagicMock) -> None:
    PsycopgAdapter(Driver.PGSQL, "app", UnixSocket("/var/run/postgresql"), (), {}, None)

    assert conninfo(fake_connect) == {"dbname": "app", "host": "/var/run/postgresql"}


def test_search_path_and_startup_queries_run_on_connect(fake_connect: MagicMock) -> None:
    PsycopgAdapter(Driver.PGSQL, "app", LOCAL, ["SET TIME ZONE 'UTC'"], {"search_path": "app, public"}, None)

    executed = [call.args for call in fake_connect.return_value.execute.call_args_list]

    assert executed == [("SET search_path TO app, public", None), ("SET TIME ZONE 'UTC'", None)]


def test_connect_errors_become_connection_failed(fake_connect: MagicMock) -> None:
    fake_connect.side_effect = psycopg.OperationalError("connection refused")

    with pytest.raises(ConnectionFailedError, match="connection refused") as error:
        PsycopgAdapter(Driver.PGSQL, "app", LOCAL, (), {}, None)

    assert isinstance(error.value.__cause__, psycopg.OperationalError)


def server_is_up(port: int) -> bool:
    try:
        socket.create_connection(("localhost", port), timeout=0.5).close()
    except OSError:
        return False

    return True


@pytest.fixture
def adapter() -> Iterator[PsycopgAdapter]:
    if not server_is_up(5432):
        pytest.skip("PostgreSQL is not reachable on localhost:5432")

    adapter = PsycopgAdapter(Driver.PGSQL, "postgres", LOCAL, (), {}, None)
    adapter.exec("CREATE TEMP TABLE adapter_users (id serial PRIMARY KEY, name text UNIQUE, value text)")
    yield adapter
    adapter.close()


@pytest.fixture
def dialect(adapter: PsycopgAdapter) -> PgSQLDialect:
    return PgSQLDialect(Driver.PGSQL, adapter.version())


@pytest.mark.integration
def test_version(dialect: PgSQLDialect) -> None:
    assert dialect.version >= 90000


@pytest.mark.integration
def test_percent_signs_without_params_are_sent_as_is(adapter: PsycopgAdapter) -> None:
    assert adapter.query("SELECT '%' AS a, '%s' AS b, '?' AS c").fetch_dict() == {"a": "%", "b": "%s", "c": "?"}


@pytest.mark.integration
def test_placeholders_with_params(adapter: PsycopgAdapter, dialect: PgSQLDialect) -> None:
    positional = QueryWithParams("SELECT ?::int AS a, '%' || ? AS b, '?' AS c", [1, "x"])
    named = QueryWithParams("SELECT :n::int AS a, :n::int + 1 AS b", {"n": 1})

    assert adapter.query_with_params(dialect, positional).fetch_dict() == {"a": 1, "b": "%x", "c": "?"}
    assert adapter.query_with_params(dialect, named).fetch_dict() == {"a": 1, "b": 2}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("value", "column_type", "expected"),
    [
        (True, "boolean", True),
        (None, "text", None),
        (Decimal("1.50"), "numeric(5, 2)", Decimal("1.50")),
        (b"\x00\x01", "bytea", b"\x00\x01"),
        (date(2024, 1, 2), "date", date(2024, 1, 2)),
        (datetime(2024, 1, 2, 3, 4, 5, 6), "timestamp", datetime(2024, 1, 2, 3, 4, 5, 6)),
    ],
)
def test_params_round_trip(
    adapter: PsycopgAdapter, dialect: PgSQLDialect, value: Any, column_type: str, expected: Any
) -> None:
    adapter.exec(f"CREATE TEMP TABLE round_trip (value {column_type})")
    adapter.query_with_params(dialect, QueryWithParams("INSERT INTO round_trip VALUES (?)", [value]))

    assert adapter.query("SELECT value FROM round_trip").scalar() == expected

    adapter.exec("DROP TABLE round_trip")


@pytest.mark.integration
def test_emulate_prepare(adapter: PsycopgAdapter, dialect: PgSQLDialect) -> None:
    query_with_params = QueryWithParams("SELECT ? AS a, ? AS b", ["it's 100%", True])

    assert adapter.query_with_params(dialect, query_with_params, emulate_prepare=True).fetch_dict() == {
        "a": "it's 100%",
        "b": True,
    }


@pytest.mark.integration
def test_result_columns(adapter: PsycopgAdapter) -> None:
    result = adapter.query("SELECT 1 AS a, 'x'::text AS b, now() AS c")

    assert result.columns() == {"a": "INT4", "b": "TEXT", "c": "TIMESTAMPTZ"}


@pytest.mark.integration
def test_transactions_and_savepoints(adapter: PsycopgAdapter, dialect: PgSQLDialect) -> None:
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
def test_failed_transaction_counts_as_in_transaction(adapter: PsycopgAdapter, dialect: PgSQLDialect) -> None:
    adapter.begin_transaction(dialect)

    with pytest.raises(ProgrammingError):
        adapter.exec("SELECT * FROM missing")

    assert adapter.in_transaction()
    adapter.rollback_transaction(dialect)
    assert not adapter.in_transaction()


@pytest.mark.integration
def test_last_insert_id(adapter: PsycopgAdapter) -> None:
    adapter.exec("INSERT INTO adapter_users (name) VALUES ('Alice'), ('Bob')")

    assert adapter.last_insert_id() == 2
    assert adapter.last_insert_id("adapter_users_id_seq") == 2
    assert adapter.last_insert_id("missing_seq") is None


@pytest.mark.integration
def test_last_insert_id_failure_keeps_the_transaction_usable(adapter: PsycopgAdapter, dialect: PgSQLDialect) -> None:
    adapter.begin_transaction(dialect)

    assert adapter.last_insert_id("missing_seq") is None
    assert adapter.query("SELECT 1").scalar() == 1

    adapter.rollback_transaction(dialect)


@pytest.mark.integration
def test_unique_violation(adapter: PsycopgAdapter, dialect: PgSQLDialect) -> None:
    insert = QueryWithParams("INSERT INTO adapter_users (name) VALUES (?)", ["Alice"])
    adapter.query_with_params(dialect, insert)

    with pytest.raises(UniqueViolationError) as error:
        adapter.query_with_params(dialect, insert)

    assert (error.value.sqlstate, error.value.query) == ("23505", "INSERT INTO adapter_users (name) VALUES (%s)")


@pytest.mark.integration
def test_search_path_option() -> None:
    if not server_is_up(5432):
        pytest.skip("PostgreSQL is not reachable on localhost:5432")

    adapter = PsycopgAdapter(Driver.PGSQL, "postgres", LOCAL, (), {"search_path": "pg_catalog"}, None)

    assert adapter.query("SHOW search_path").scalar() == "pg_catalog"
    adapter.close()


@pytest.mark.integration
def test_connection_per_thread(adapter: PsycopgAdapter) -> None:
    pids: list[int] = []
    thread = threading.Thread(target=lambda: pids.append(adapter.query("SELECT pg_backend_pid()").scalar()))
    thread.start()
    thread.join()

    assert pids[0] != adapter.query("SELECT pg_backend_pid()").scalar()
