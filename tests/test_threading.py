"""Per-thread connections, the connection cap and the shared in-memory SQLite connection."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from sentiencedb import Database, connect_sqlite
from sentiencedb.adapters._thread_local_store import ThreadLocalStore
from sentiencedb.exceptions import AdapterError, ConnectionLimitError


def run_in_thread(function: Callable[[], Any]) -> Any:
    """Run ``function`` in a new thread, wait for it and return its result (or raise its exception)."""
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


class Values:
    """Creates numbered values for a store and records which ones were disposed of."""

    def __init__(self) -> None:
        self.created = 0
        self.disposed: list[int] = []

    def create(self) -> int:
        self.created += 1
        return self.created

    def dispose(self, value: int) -> None:
        self.disposed.append(value)


@pytest.fixture
def values() -> Values:
    return Values()


def test_store_keeps_one_value_per_thread(values: Values) -> None:
    store = ThreadLocalStore(values.create, values.dispose)

    assert store.get() == store.get() == 1
    assert run_in_thread(store.get) == 2
    assert store.get() == 1


def test_store_disposes_of_a_value_when_its_thread_exits(values: Values) -> None:
    store = ThreadLocalStore(values.create, values.dispose)
    store.get()

    run_in_thread(store.get)

    assert values.disposed == [2]
    assert len(store) == 1


def test_store_sweeps_values_of_finished_threads(values: Values) -> None:
    store = ThreadLocalStore(values.create, values.dispose)

    def get_without_exit_hook() -> None:
        store.get()
        store._thread_slot.exit_hook._callback = lambda: None  # type: ignore[union-attr]

    run_in_thread(get_without_exit_hook)
    assert len(store) == 1

    run_in_thread(store.get)

    assert values.disposed[0] == 1


def test_shared_store_uses_one_value_for_every_thread(values: Values) -> None:
    store = ThreadLocalStore(values.create, values.dispose, shared=True)

    assert store.get() == run_in_thread(store.get) == 1
    assert values.disposed == []


def test_store_cap_times_out(values: Values) -> None:
    store = ThreadLocalStore(values.create, values.dispose, max_values=1, acquire_timeout=0.01)
    store.get()

    with pytest.raises(ConnectionLimitError):
        run_in_thread(store.get)


def test_store_cap_waits_for_a_free_slot(values: Values) -> None:
    store = ThreadLocalStore(values.create, values.dispose, max_values=1, acquire_timeout=5)
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        store.get()
        holding.set()
        release.wait()

    holder = threading.Thread(target=hold)
    holder.start()
    holding.wait()
    threading.Timer(0.05, release.set).start()

    assert store.get() == 2
    holder.join()
    assert values.disposed == [1]


def test_failed_create_releases_its_slot() -> None:
    attempts: list[int] = []

    def create() -> int:
        attempts.append(len(attempts) + 1)

        if len(attempts) == 1:
            raise RuntimeError("down")

        return len(attempts)

    store = ThreadLocalStore(create, lambda value: None, max_values=1, acquire_timeout=0.01)

    with pytest.raises(RuntimeError):
        store.get()

    assert store.get() == 2


def test_store_cap_must_be_positive(values: Values) -> None:
    with pytest.raises(AdapterError):
        ThreadLocalStore(values.create, values.dispose, max_values=0)


def test_closed_store_disposes_of_everything(values: Values) -> None:
    store = ThreadLocalStore(values.create, values.dispose, max_values=2)
    store.get()
    store.close()

    assert values.disposed == [1]

    with pytest.raises(AdapterError):
        store.get()


@pytest.fixture
def file_db(tmp_path: Path) -> Iterator[Database]:
    with connect_sqlite(str(tmp_path / "threads.sqlite"), queries=["CREATE TEMP TABLE marker (x)"]) as db:
        db.exec("CREATE TABLE users (name TEXT)")
        yield db


def test_file_database_has_a_connection_per_thread(file_db: Database) -> None:
    connection = file_db._adapter._connection()  # type: ignore[attr-defined]
    other_connection = run_in_thread(file_db._adapter._connection)  # type: ignore[attr-defined]

    assert other_connection is not connection
    assert run_in_thread(lambda: file_db.query("SELECT count(*) FROM marker").scalar()) == 0


def test_transactions_belong_to_their_thread(file_db: Database) -> None:
    file_db.begin_transaction()
    file_db.exec("INSERT INTO users VALUES ('Alice')")

    assert run_in_thread(file_db.in_transaction) is False
    assert run_in_thread(lambda: file_db.query("SELECT count(*) FROM users").scalar()) == 0

    file_db.commit_transaction()

    assert run_in_thread(lambda: file_db.query("SELECT count(*) FROM users").scalar()) == 1


def test_savepoint_stacks_belong_to_their_thread(tmp_path: Path) -> None:
    statements: list[str] = []
    barrier = threading.Barrier(2)

    def nest(db: Database) -> None:
        db.begin_transaction()
        barrier.wait()
        db.begin_transaction()
        barrier.wait()
        db.commit_transaction()
        db.commit_transaction()

    with connect_sqlite(str(tmp_path / "db.sqlite"), debug=lambda q, s, e: statements.append(q)) as db:
        threads = [threading.Thread(target=nest, args=(db,)) for _ in range(2)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

    assert sorted(statements) == sorted(
        ["BEGIN TRANSACTION", 'SAVEPOINT "savepoint_1"', 'RELEASE SAVEPOINT "savepoint_1"', "COMMIT"] * 2
    )


def test_connection_cap(tmp_path: Path) -> None:
    options: Any = {"max_connections": 1, "acquire_timeout": 0.01}

    with connect_sqlite(str(tmp_path / "db.sqlite"), options=options) as db:
        db.query("SELECT 1")  # the main thread holds the only slot from here on

        with pytest.raises(ConnectionLimitError):
            run_in_thread(lambda: db.query("SELECT 1"))

        assert db.query("SELECT 1").scalar() == 1


def test_connections_of_finished_threads_are_closed(file_db: Database) -> None:
    store = file_db._adapter._connections  # type: ignore[attr-defined]

    run_in_thread(lambda: file_db.query("SELECT 1"))

    assert len(store) == 1


def test_in_memory_database_is_shared_by_every_thread() -> None:
    with connect_sqlite(":memory:") as db:
        db.exec("CREATE TABLE numbers (n INTEGER)")
        threads = [
            threading.Thread(target=lambda n=n: db.prepared("INSERT INTO numbers VALUES (?)", [n])) for n in range(20)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert db.query("SELECT count(*) FROM numbers").scalar() == 20
        assert len(db._adapter._connections) == 1  # type: ignore[attr-defined]
