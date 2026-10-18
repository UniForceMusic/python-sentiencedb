"""The Database facade on a real SQLite database, through raw SQL only."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, call, create_autospec

import pytest

import sentiencedb.database as database_module
from sentiencedb import Database, Driver, NetworkSocket, UnixSocket
from sentiencedb.adapters import SQLite3Adapter
from sentiencedb.dialects import SQLiteDialect
from sentiencedb.exceptions import AdapterError
from sentiencedb.queries.objects.alias import Alias, SubQuery
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.schemas import SQLiteSchema
from sentiencedb.schemas.schema_abc import SchemaABC


@pytest.fixture
def statements() -> list[str]:
    return []


@pytest.fixture
def db(statements: list[str]) -> Iterator[Database]:
    with Database.connect_sqlite(debug=lambda query, start, error: statements.append(query)) as db:
        db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
        statements.clear()
        yield db


def names(db: Database) -> list[str]:
    return [row["name"] for row in db.query("SELECT name FROM users ORDER BY id")]


def test_connect_sqlite_builds_the_sqlite_stack(db: Database) -> None:
    assert isinstance(db._adapter, SQLite3Adapter)
    assert isinstance(db._schema, SQLiteSchema)
    assert isinstance(db.dialect, SQLiteDialect)
    assert db.dialect.version > 30000


def test_connect_passes_queries_and_options(tmp_path: Path) -> None:
    with Database.connect(
        Driver.SQLITE, str(tmp_path / "db.sqlite"), queries=["CREATE TABLE t (id INTEGER)"], options={"strict": True}
    ) as db:
        assert db.query("SELECT count(*) FROM t").scalar() == 0
        assert db.dialect.options == {"strict": True}


@pytest.mark.parametrize(
    ("method", "driver", "port"),
    [
        ("connect_postgresql", Driver.PGSQL, 5432),
        ("connect_mysql", Driver.MYSQL, 3306),
        ("connect_mariadb", Driver.MARIADB, 3306),
    ],
)
def test_server_helpers_build_sockets(monkeypatch: pytest.MonkeyPatch, method: str, driver: Driver, port: int) -> None:
    connect = MagicMock()
    monkeypatch.setattr(Database, "connect", connect)
    options = {"charset": "utf8mb4"}

    getattr(Database, method)("app", username="user", password="secret", queries=["SELECT 1"], options=options)
    getattr(Database, method)("app", host="db", port=1, unix_socket="/run/db.sock")

    assert connect.call_args_list[0].args == (
        driver,
        "app",
        NetworkSocket("localhost", port, "user", "secret"),
        ["SELECT 1"],
        options,
        None,
    )
    assert connect.call_args_list[1].args[2] == UnixSocket("/run/db.sock")


def test_exec_query_and_prepared(db: Database) -> None:
    db.exec("INSERT INTO users (name) VALUES ('Alice')")
    db.prepared("INSERT INTO users (name) VALUES (?)", ["Bob"])
    db.prepared("INSERT INTO users (name) VALUES (:name)", {"name": "Carol"})

    assert names(db) == ["Alice", "Bob", "Carol"]
    assert db.prepared("SELECT name FROM users WHERE id = ?", [2]).scalar() == "Bob"


def test_query_with_params_without_params_sends_plain_sql(db: Database, statements: list[str]) -> None:
    result = db.query_with_params(QueryWithParams("SELECT '?' AS q, ':x' AS n"))

    assert result.fetch_dict() == {"q": "?", "n": ":x"}
    assert statements == ["SELECT '?' AS q, ':x' AS n"]


def test_emulate_prepare(db: Database, statements: list[str]) -> None:
    db.prepared("INSERT INTO users (name) VALUES (?)", ["O'Brien"], emulate_prepare=True)

    assert names(db) == ["O'Brien"]
    assert statements[0] == "INSERT INTO users (name) VALUES ('O''Brien')"


def test_last_insert_id(db: Database) -> None:
    db.prepared("INSERT INTO users (name) VALUES (?)", ["Alice"])

    assert db.last_insert_id() == 1


def test_commit_and_rollback(db: Database) -> None:
    db.begin_transaction()
    db.exec("INSERT INTO users (name) VALUES ('Alice')")
    assert db.in_transaction()
    db.commit_transaction()

    db.begin_transaction()
    db.exec("INSERT INTO users (name) VALUES ('Bob')")
    db.rollback_transaction()

    assert not db.in_transaction()
    assert names(db) == ["Alice"]


def test_commit_and_rollback_outside_a_transaction_are_no_ops(db: Database, statements: list[str]) -> None:
    db.commit_transaction()
    db.rollback_transaction()

    assert statements == []


def test_nested_transactions_use_savepoints(db: Database, statements: list[str]) -> None:
    db.begin_transaction()
    db.exec("INSERT INTO users (name) VALUES ('Alice')")
    db.begin_transaction()
    db.exec("INSERT INTO users (name) VALUES ('Bob')")
    db.begin_transaction("named")
    db.exec("INSERT INTO users (name) VALUES ('Carol')")
    db.rollback_transaction()
    db.commit_transaction()
    db.commit_transaction()

    assert [statement for statement in statements if not statement.startswith("INSERT")] == [
        "BEGIN TRANSACTION",
        'SAVEPOINT "savepoint_1"',
        'SAVEPOINT "named"',
        'ROLLBACK TO "named"',
        'RELEASE SAVEPOINT "savepoint_1"',
        "COMMIT",
    ]
    assert names(db) == ["Alice", "Bob"]


def test_release_savepoints_ends_the_whole_transaction(db: Database) -> None:
    db.begin_transaction()
    db.exec("INSERT INTO users (name) VALUES ('Alice')")
    db.begin_transaction()
    db.begin_transaction()
    db.rollback_transaction(release_savepoints=True)

    assert not db.in_transaction()
    assert names(db) == []

    db.begin_transaction()
    db.begin_transaction()
    db.exec("INSERT INTO users (name) VALUES ('Bob')")
    db.commit_transaction(release_savepoints=True)

    assert not db.in_transaction()
    assert names(db) == ["Bob"]


def test_transaction_commits_and_returns_the_callback_result(db: Database) -> None:
    result = db.transaction(lambda db: db.prepared("INSERT INTO users (name) VALUES (?)", ["Alice"]) and "done")

    assert result == "done"
    assert not db.in_transaction()
    assert names(db) == ["Alice"]


def test_transaction_rolls_back_and_reraises(db: Database) -> None:
    def fail(db: Database) -> None:
        db.exec("INSERT INTO users (name) VALUES ('Alice')")
        raise KeyError("boom")

    with pytest.raises(KeyError, match="boom"):
        db.transaction(fail)

    assert not db.in_transaction()
    assert names(db) == []


def test_nested_transaction_rolls_back_only_the_inner_part(db: Database) -> None:
    def outer(db: Database) -> None:
        db.exec("INSERT INTO users (name) VALUES ('Alice')")

        with pytest.raises(ValueError):
            db.transaction(inner)

    def inner(db: Database) -> None:
        db.exec("INSERT INTO users (name) VALUES ('Bob')")
        raise ValueError

    db.transaction(outer)

    assert names(db) == ["Alice"]


def test_close_and_context_manager() -> None:
    with Database.connect_sqlite() as db:
        assert db.query("SELECT 1").scalar() == 1

    with pytest.raises(AdapterError):
        db.query("SELECT 1")


def test_builders_are_bound_to_the_database(monkeypatch: pytest.MonkeyPatch, db: Database) -> None:
    builders = {
        name: MagicMock(name=name)
        for name in (
            "SelectQuery",
            "InsertQuery",
            "UpdateQuery",
            "DeleteQuery",
            "CreateTableQuery",
            "AlterTableQuery",
            "DropTableQuery",
            "CreateIndexQuery",
            "DropIndexQuery",
            "QueryFactory",
        )
    }

    for name, builder in builders.items():
        monkeypatch.setattr(database_module, name, builder)

    select = MagicMock()
    dialect = db.dialect

    assert db.select("users") is builders["SelectQuery"].return_value
    db.select_table("users")
    db.select_table(["main", "users"], "u")
    db.select_sub_query(select, "s")
    db.insert("users")
    db.update("users")
    db.delete("users")
    db.create_table("users")
    db.alter_table("users")
    db.drop_table("users")
    db.create_index("users", "index")
    db.drop_index("users", "index")
    db.table("users")

    assert [call.args for call in builders["SelectQuery"].call_args_list] == [
        (db, dialect, "users"),
        (db, dialect, "users"),
        (db, dialect, Alias(["main", "users"], "u")),
        (db, dialect, SubQuery(select, "s")),
    ]

    for name in ("InsertQuery", "UpdateQuery", "DeleteQuery", "CreateTableQuery", "AlterTableQuery", "DropTableQuery"):
        builders[name].assert_called_once_with(db, dialect, "users")

    builders["CreateIndexQuery"].assert_called_once_with(db, dialect, "users", "index")
    builders["DropIndexQuery"].assert_called_once_with(db, dialect, "users", "index")
    builders["QueryFactory"].assert_called_once_with(db, "users")


def test_information_schema_delegates_to_the_schema(db: Database) -> None:
    schema = create_autospec(SchemaABC, instance=True)
    db = Database(db._adapter, db.dialect, schema)

    db.information_schema_schemas()
    db.information_schema_tables()
    db.information_schema_tables("main")
    db.information_schema_table_exists("users")
    db.information_schema_table_exists("users", "main")
    db.information_schema_index_exists("users", "ix")
    db.information_schema_index_exists("users", "ix", "main")
    db.information_schema_columns("users")
    db.information_schema_primary_keys("users")
    db.information_schema_unique_constraints("users")
    db.information_schema_foreign_key_constraints("users")
    db.information_schema_indexes("users")

    schema.schemas.assert_called_once_with(db, db.dialect)
    assert schema.tables.call_args_list == [call(db, db.dialect, None), call(db, db.dialect, "main")]
    assert schema.table_exists.call_args_list == [
        call(db, db.dialect, "users", None),
        call(db, db.dialect, "users", "main"),
    ]
    assert schema.index_exists.call_args_list == [
        call(db, db.dialect, "users", "ix", None),
        call(db, db.dialect, "users", "ix", "main"),
    ]

    for method in ("columns", "primary_keys", "unique_constraints", "foreign_key_constraints", "indexes"):
        getattr(schema, method).assert_called_once_with(db, db.dialect, "users")
