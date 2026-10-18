"""Regression tests for the adapter / Database review findings (H1, M1-M7, L3-L8, S3, S4).

The SQLite tests need nothing; the server tests run against the docker compose services (PostgreSQL on 5432, MySQL
on 3306, MariaDB on 3307) in a dedicated ``sentiencedb_regressions`` database and are skipped when a server is
unreachable.
"""

from __future__ import annotations

import enum
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec

import pytest

from sentiencedb import (
    AdapterError,
    Database,
    DataError,
    Driver,
    DriverError,
    NotNullViolationError,
    OperationalError,
    UniqueViolationError,
)
from sentiencedb.adapters._thread_local_store import ThreadLocalStore
from sentiencedb.adapters.adapter_abc import AdapterABC
from sentiencedb.dialects import SQLiteDialect
from sentiencedb.schemas.schema_abc import SchemaABC

DATABASE = "sentiencedb_regressions"

AWARE = datetime(2024, 1, 1, 12, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))


class Color(enum.Enum):
    RED = "red"


def run_in_thread(function: Callable[[], Any]) -> Any:
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = function()
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()

    if "error" in outcome:
        raise outcome["error"]

    return outcome["result"]


@pytest.fixture
def file_db(tmp_path: Path) -> Iterator[Database]:
    with Database.connect_sqlite(str(tmp_path / "db.sqlite"), options={"busy_timeout": 200}) as db:
        db.exec("CREATE TABLE t (id INTEGER)")
        yield db


def ids(db: Database) -> list[int]:
    return [row["id"] for row in db.query("SELECT id FROM t ORDER BY id")]


# -- H1: transaction() ends exactly its own level ----------------------------------------------------------------


def test_unbalanced_savepoint_left_by_a_failing_callback_is_rolled_back(file_db: Database) -> None:
    def callback(db: Database) -> None:
        db.exec("INSERT INTO t VALUES (1)")
        db.begin_transaction()  # a helper that opens a savepoint and fails before closing it
        db.exec("INSERT INTO t VALUES (2)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        file_db.transaction(callback)

    assert file_db.in_transaction() is False
    assert ids(file_db) == []
    assert file_db._thread.savepoints == []  # type: ignore[attr-defined]


def test_unbalanced_savepoint_left_by_a_succeeding_callback_is_committed(file_db: Database) -> None:
    def callback(db: Database) -> None:
        db.begin_transaction()
        db.exec("INSERT INTO t VALUES (1)")

    file_db.transaction(callback)

    assert file_db.in_transaction() is False
    assert ids(file_db) == [1]


def test_nested_transaction_rolls_back_only_its_own_savepoint(file_db: Database) -> None:
    def inner(db: Database) -> None:
        db.exec("INSERT INTO t VALUES (2)")
        db.begin_transaction()  # left open
        db.exec("INSERT INTO t VALUES (3)")
        raise RuntimeError("inner")

    def outer(db: Database) -> None:
        db.exec("INSERT INTO t VALUES (1)")

        with pytest.raises(RuntimeError):
            db.transaction(inner)

        assert db.in_transaction() is True
        assert db._thread.savepoints == []  # type: ignore[attr-defined]
        db.exec("INSERT INTO t VALUES (4)")

    file_db.transaction(outer)

    assert ids(file_db) == [1, 4]


def stub_database(**adapter_overrides: Any) -> tuple[Database, Any]:
    adapter = create_autospec(AdapterABC, instance=True)
    adapter.in_transaction.return_value = True

    for name, value in adapter_overrides.items():
        setattr(adapter, name, value)

    return Database(adapter, SQLiteDialect(Driver.SQLITE, "3.45.1"), create_autospec(SchemaABC)), adapter


def test_savepoint_is_pushed_only_after_it_was_created() -> None:
    db, adapter = stub_database()
    adapter.begin_savepoint.side_effect = OperationalError("no")

    with pytest.raises(OperationalError):
        db.begin_transaction()

    assert db._thread.savepoints == []  # type: ignore[attr-defined]


def test_failing_rollback_does_not_replace_the_original_error() -> None:
    db, adapter = stub_database()
    adapter.in_transaction.return_value = False
    adapter.begin_transaction.side_effect = lambda dialect: setattr(adapter.in_transaction, "return_value", True)
    adapter.rollback_transaction.side_effect = OperationalError("connection lost")

    def callback(db: Database) -> None:
        raise ValueError("root cause")

    with pytest.raises(ValueError, match="root cause") as error:
        db.transaction(callback)

    assert any("connection lost" in note for note in error.value.__notes__)


def test_failing_commit_rolls_back_and_raises_the_commit_error() -> None:
    db, adapter = stub_database()
    adapter.in_transaction.return_value = False
    adapter.begin_transaction.side_effect = lambda dialect: setattr(adapter.in_transaction, "return_value", True)
    adapter.commit_transaction.side_effect = OperationalError("deferred constraint")

    with pytest.raises(OperationalError, match="deferred constraint"):
        db.transaction(lambda db: None)

    adapter.rollback_transaction.assert_called_once()


# -- S3 / S4: savepoint support and names -----------------------------------------------------------------------


def test_nesting_without_savepoint_support_raises() -> None:
    db, _ = stub_database()

    class NoSavepoints(SQLiteDialect):
        @property
        def savepoints(self) -> bool:
            return False

    db._dialect = NoSavepoints(Driver.SQLITE, "3.45.1")  # type: ignore[attr-defined]

    with pytest.raises(AdapterError):
        db.begin_transaction()


def test_commit_and_rollback_by_savepoint_name(tmp_path: Path) -> None:
    statements: list[str] = []

    with Database.connect_sqlite(str(tmp_path / "db.sqlite"), debug=lambda q, s, e: statements.append(q)) as db:
        db.exec("CREATE TABLE t (id INTEGER)")
        db.begin_transaction()
        db.begin_transaction("a")
        db.exec("INSERT INTO t VALUES (1)")
        db.begin_transaction("b")
        db.begin_transaction("c")
        db.rollback_transaction(name="b")  # ROLLBACK TO b, dropping c

        assert db._thread.savepoints == ["a"]  # type: ignore[attr-defined]

        with pytest.raises(AdapterError):
            db.commit_transaction(name="missing")

        db.commit_transaction(name="a")
        db.commit_transaction()

        assert db.query("SELECT id FROM t").fetch_dicts() == [{"id": 1}]

    assert 'ROLLBACK TO "b"' in statements
    assert 'RELEASE SAVEPOINT "a"' in statements


# -- M2 / L8: the debug callback never raises --------------------------------------------------------------------


def test_debug_with_values_without_a_literal_does_not_fail_the_statement() -> None:
    log: list[tuple[str, str | None]] = []

    with Database.connect_sqlite(debug=lambda q, s, e: log.append((q, e))) as db:
        db.exec("CREATE TABLE t (x REAL)")
        db.prepared("INSERT INTO t VALUES (?)", [float("inf")])

        assert db.query("SELECT x FROM t").scalar() == float("inf")
        assert log[1] == ("INSERT INTO t VALUES (?) -- params: [inf]", None)

        with pytest.raises(OperationalError, match="no such table"):
            db.prepared("INSERT INTO missing VALUES (?)", [float("nan")])

        assert log[-1][1] == "no such table: missing"


def test_debug_sees_errors_raised_while_computing_rows() -> None:
    log: list[str | None] = []
    options: Any = {"create_functions": {"boom": lambda v: 1 // (v - 2)}}

    with (
        Database.connect_sqlite(debug=lambda q, s, e: log.append(e), options=options) as db,
        pytest.raises(DriverError),
    ):
        db.query("SELECT boom(value) AS b FROM (SELECT 1 AS value UNION ALL SELECT 2)")

    assert log[-1] is not None


# -- M3: shared in-memory database ------------------------------------------------------------------------------


def test_shared_memory_transactions_do_not_mix() -> None:
    with Database.connect_sqlite(":memory:") as db:
        db.exec("CREATE TABLE t (who TEXT)")
        a_inside = threading.Event()
        b_started = threading.Event()
        b_seen: list[bool] = []

        def a() -> None:
            def callback(db: Database) -> None:
                db.exec("INSERT INTO t VALUES ('a')")
                a_inside.set()
                b_started.wait(5)
                time.sleep(0.05)  # b is now blocked on the connection
                raise RuntimeError("a fails")

            with pytest.raises(RuntimeError):
                db.transaction(callback)

        def b() -> None:
            a_inside.wait(5)
            b_seen.append(db.in_transaction())  # a's transaction is not b's
            b_started.set()
            db.transaction(lambda db: db.exec("INSERT INTO t VALUES ('b')"))

        threads = [threading.Thread(target=a), threading.Thread(target=b)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(10)

        assert b_seen == [False]
        assert db.query("SELECT who FROM t").fetch_dicts() == [{"who": "b"}]
        assert db.in_transaction() is False


def test_shared_memory_statements_of_other_threads_wait_for_the_transaction() -> None:
    with Database.connect_sqlite(":memory:") as db:
        db.exec("CREATE TABLE t (who TEXT)")
        db.begin_transaction()
        db.exec("INSERT INTO t VALUES ('main')")
        other = threading.Thread(target=lambda: db.exec("INSERT INTO t VALUES ('other')"))
        other.start()
        other.join(0.1)

        assert other.is_alive()  # waiting for the transaction

        db.rollback_transaction()
        other.join(5)

        assert db.query("SELECT who FROM t").fetch_dicts() == [{"who": "other"}]


# -- M4 / L6: parameter normalisation ----------------------------------------------------------------------------


@pytest.mark.parametrize("emulate_prepare", [False, True])
def test_sqlite_stores_aware_datetimes_in_utc(emulate_prepare: bool) -> None:
    with Database.connect_sqlite() as db:
        value = db.prepared("SELECT ? AS v", [AWARE], emulate_prepare).scalar()

    assert value == "2024-01-01 10:00:00"  # UTC, second precision


def test_enum_members_are_bound_by_value() -> None:
    with Database.connect_sqlite() as db:
        assert db.prepared("SELECT ? AS v", [Color.RED]).scalar() == "red"  # type: ignore[list-item]
        assert db.prepared("SELECT :c AS v", {"c": Color.RED}).scalar() == "red"  # type: ignore[dict-item]


# -- M6: SQLite results are read eagerly ------------------------------------------------------------------------


def test_partially_read_result_does_not_block_writers(file_db: Database) -> None:
    for number in range(5):
        file_db.prepared("INSERT INTO t VALUES (?)", [number])

    result = file_db.query("SELECT id FROM t ORDER BY id")
    assert result.fetch_dict() == {"id": 0}

    run_in_thread(lambda: file_db.prepared("INSERT INTO t VALUES (?)", [99]))

    assert result.fetch_dicts() == [{"id": n} for n in range(1, 5)]


# -- M7: releasing connections ---------------------------------------------------------------------------------


def test_store_release_current_frees_the_slot() -> None:
    disposed: list[int] = []
    created = iter(range(1, 100))
    store = ThreadLocalStore(lambda: next(created), disposed.append, max_values=1, acquire_timeout=0.01)

    assert store.get() == 1
    assert store.current() == 1
    assert store.release_current() is True
    assert store.current() is None
    assert disposed == [1]
    assert run_in_thread(lambda: (store.get(), store.release_current())[0]) == 2
    assert store.release_current() is False
    assert store.get() == 3


def test_shared_store_never_releases() -> None:
    store = ThreadLocalStore(lambda: 1, lambda value: None, shared=True)
    store.get()

    assert store.release_current() is False
    assert store.current() == 1


def test_connect_does_not_pin_a_slot(tmp_path: Path) -> None:
    options: Any = {"max_connections": 1, "acquire_timeout": 0.5}

    with Database.connect_sqlite(str(tmp_path / "db.sqlite"), options=options) as db:
        assert len(db._adapter._connections) == 0  # type: ignore[attr-defined]
        assert run_in_thread(lambda: db.query("SELECT 1 AS one").scalar()) == 1


def test_thread_pool_with_more_workers_than_connections(tmp_path: Path) -> None:
    options: Any = {"max_connections": 2, "acquire_timeout": 10, "journal_mode": "WAL"}

    with Database.connect_sqlite(str(tmp_path / "db.sqlite"), options=options) as db:
        with db.connection():
            db.exec("CREATE TABLE t (id INTEGER)")

        def job(number: int) -> None:
            with db.connection():
                db.prepared("INSERT INTO t VALUES (?)", [number])
                time.sleep(0.01)

        with ThreadPoolExecutor(8) as pool:
            list(pool.map(job, range(32)))

        assert len(db._adapter._connections) == 0  # type: ignore[attr-defined]
        assert db.query("SELECT count(*) AS n FROM t").scalar() == 32


def test_connection_context_keeps_an_open_transaction_and_nests(file_db: Database) -> None:
    store = file_db._adapter._connections  # type: ignore[attr-defined]

    with file_db.connection() as db:
        with db.connection():
            db.exec("INSERT INTO t VALUES (1)")

        assert store.current() is not None  # the outer block still uses it

    assert store.current() is None

    with file_db.connection():
        file_db.begin_transaction()

    assert store.current() is not None

    with pytest.raises(AdapterError):
        file_db.release_connection()

    file_db.rollback_transaction()
    file_db.release_connection()

    assert store.current() is None
    file_db.release_connection()  # no connection: no-op


def test_in_memory_connection_is_never_released() -> None:
    with Database.connect_sqlite() as db:
        db.exec("CREATE TABLE t (id INTEGER)")
        db.release_connection()

        assert db.query("SELECT count(*) FROM t").scalar() == 0


# -- L3 / L4 ------------------------------------------------------------------------------------------------------


def test_sqlite_last_insert_id_is_none_before_any_insert() -> None:
    with Database.connect_sqlite() as db:
        assert db.last_insert_id() is None


@pytest.mark.parametrize(("method", "extra"), [("connect_postgresql", "postgres"), ("connect_mysql", "mysql")])
def test_missing_driver_package_names_the_extra(monkeypatch: pytest.MonkeyPatch, method: str, extra: str) -> None:
    for module in ("psycopg", "mysql", "mysql.connector"):
        monkeypatch.setitem(sys.modules, module, None)

    with pytest.raises(AdapterError, match=rf"sentiencedb\[{extra}\]") as error:
        getattr(Database, method)("x")

    assert isinstance(error.value.__cause__, ImportError)
    assert error.value.__cause__.__context__ is None


# -- live servers -------------------------------------------------------------------------------------------------


def server_is_up(port: int) -> bool:
    try:
        socket.create_connection(("localhost", port), timeout=0.5).close()
    except OSError:
        return False

    return True


SERVERS = {
    "pgsql": (Database.connect_postgresql, 5432, "postgres", "postgres", "psycopg"),
    "mysql": (Database.connect_mysql, 3306, "mysql", "root", "mysql.connector"),
    "mariadb": (Database.connect_mariadb, 3307, "mysql", "root", "mysql.connector"),
}


def connect_server(name: str) -> Iterator[Database]:
    connect, port, admin_database, username, module = SERVERS[name]
    pytest.importorskip(module)

    if not server_is_up(port):
        pytest.skip(f"{name} is not reachable on localhost:{port}")

    with connect(admin_database, port=port, username=username) as admin:
        admin.exec(f"DROP DATABASE IF EXISTS {DATABASE}")
        admin.exec(f"CREATE DATABASE {DATABASE}")

    with connect(DATABASE, port=port, username=username) as db:
        yield db

    with connect(admin_database, port=port, username=username) as admin:
        admin.exec(f"DROP DATABASE IF EXISTS {DATABASE}")


@pytest.fixture
def pg() -> Iterator[Database]:
    yield from connect_server("pgsql")


@pytest.fixture(params=["mysql", "mariadb"])
def my(request: pytest.FixtureRequest) -> Iterator[Database]:
    yield from connect_server(request.param)


@pytest.mark.integration
def test_pg_commit_of_an_aborted_transaction_raises(pg: Database) -> None:
    pg.exec("CREATE TABLE t (id int PRIMARY KEY)")

    def work(db: Database) -> None:
        db.prepared("INSERT INTO t VALUES (?)", [1])

        with pytest.raises(UniqueViolationError):
            db.prepared("INSERT INTO t VALUES (?)", [1])

    with pytest.raises(OperationalError, match="aborted") as error:
        pg.transaction(work)

    assert error.value.sqlstate == "25P02"
    assert pg.in_transaction() is False
    assert pg.query("SELECT count(*) FROM t").scalar() == 0


@pytest.mark.integration
def test_pg_failed_savepoint_in_an_aborted_transaction_leaves_no_transaction(pg: Database) -> None:
    pg.exec("CREATE TABLE t (id int PRIMARY KEY)")

    def outer(db: Database) -> None:
        db.prepared("INSERT INTO t VALUES (?)", [1])

        with pytest.raises(UniqueViolationError):
            db.prepared("INSERT INTO t VALUES (?)", [1])

        db.transaction(lambda db: None)  # SAVEPOINT fails: the transaction is aborted

    with pytest.raises(DriverError, match="aborted"):
        pg.transaction(outer)

    assert pg._thread.savepoints == []  # type: ignore[attr-defined]
    assert pg.in_transaction() is False
    assert pg.query("SELECT 1 AS one").scalar() == 1


@pytest.mark.integration
def test_pg_aborted_savepoint_is_rolled_back_and_the_outer_transaction_continues(pg: Database) -> None:
    pg.exec("CREATE TABLE t (id int PRIMARY KEY)")

    def inner(db: Database) -> None:
        db.prepared("INSERT INTO t VALUES (?)", [2])

        with pytest.raises(UniqueViolationError):
            db.prepared("INSERT INTO t VALUES (?)", [2])

    def outer(db: Database) -> None:
        db.prepared("INSERT INTO t VALUES (?)", [1])

        with pytest.raises(OperationalError, match="savepoint"):
            db.transaction(inner)

        db.prepared("INSERT INTO t VALUES (?)", [3])

    pg.transaction(outer)

    assert [row["id"] for row in pg.query("SELECT id FROM t ORDER BY id")] == [1, 3]


@pytest.mark.integration
def test_pg_debug_with_driver_types_does_not_fail_the_statement(pg: Database) -> None:
    log: list[str] = []
    pg._adapter._debug = lambda q, s, e: log.append(q)  # type: ignore[attr-defined]
    pg.exec("CREATE TABLE u (id uuid PRIMARY KEY)")
    pg.prepared("INSERT INTO u VALUES (?)", [uuid.uuid4()])  # type: ignore[list-item]

    assert pg.query("SELECT count(*) FROM u").scalar() == 1
    assert log[1].startswith("INSERT INTO u VALUES (?) -- params: [UUID(")


@pytest.mark.integration
def test_pg_keeps_the_offset_of_aware_datetimes_and_binds_enums_by_value(pg: Database) -> None:
    assert pg.prepared("SELECT ?::timestamptz AS v", [AWARE]).scalar() == AWARE
    assert pg.prepared("SELECT ?::text AS v", [Color.RED]).scalar() == "red"  # type: ignore[list-item]


@pytest.mark.integration
def test_pg_array_column_types(pg: Database) -> None:
    assert pg.query("SELECT ARRAY[1, 2] AS a, 'x'::text AS b").columns() == {"a": "INT4[]", "b": "TEXT"}


@pytest.mark.integration
@pytest.mark.parametrize("emulate_prepare", [False, True])
def test_mysql_stores_aware_datetimes_in_utc(my: Database, emulate_prepare: bool) -> None:
    my.exec("CREATE TABLE d (a datetime(6))")
    my.prepared("INSERT INTO d VALUES (?)", [AWARE], emulate_prepare)

    expected = AWARE.astimezone(UTC).replace(tzinfo=None)

    assert my.query("SELECT a FROM d").scalar() == expected


@pytest.mark.integration
def test_mysql_not_null_and_bad_value_errors(my: Database) -> None:
    my.exec("CREATE TABLE e (id integer PRIMARY KEY, n varchar(3) NOT NULL, v int)")

    with pytest.raises(NotNullViolationError):
        my.exec("INSERT INTO e (id) VALUES (1)")

    with pytest.raises(DataError):
        my.exec("INSERT INTO e (id, n, v) VALUES (3, 'a', 'x')")


@pytest.mark.integration
def test_mysql_last_insert_id_is_none_before_any_insert(my: Database) -> None:
    assert my.last_insert_id() is None


@pytest.mark.integration
def test_server_thread_pool_with_more_workers_than_connections(pg: Database) -> None:
    pg.exec("CREATE TABLE t (id int)")
    options: Any = {"max_connections": 2, "acquire_timeout": 10}
    backend_ids: set[int] = set()

    with Database.connect_postgresql(DATABASE, username="postgres", options=options) as db:

        def job(number: int) -> None:
            with db.connection():
                db.prepared("INSERT INTO t VALUES (?)", [number])
                backend_ids.add(db.query("SELECT pg_backend_pid() AS pid").scalar())

        with ThreadPoolExecutor(6) as pool:
            list(pool.map(job, range(24)))

        assert len(db._adapter._connections) == 0  # type: ignore[attr-defined]

    assert pg.query("SELECT count(*) FROM t").scalar() == 24
    assert len(backend_ids) >= 2
