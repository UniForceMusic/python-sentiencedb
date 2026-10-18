"""``QueryFactory``: builders and introspection bound to one table."""

from __future__ import annotations

from typing import Any

import pytest

from sentiencedb.queries import (
    AlterTableQuery,
    CreateIndexQuery,
    CreateTableQuery,
    DeleteQuery,
    DropIndexQuery,
    DropTableQuery,
    InsertQuery,
    QueryFactory,
    SelectQuery,
    UpdateQuery,
)
from sentiencedb.queries.objects.alias import Alias
from sentiencedb.queries.objects.sql import Raw
from sentiencedb.queries.type_aliases import IdentifierLike

TABLE = ["app", "users"]


@pytest.fixture
def database(mock_database: Any, recording_dialect: Any) -> Any:
    mock_database.dialect = recording_dialect

    return mock_database


@pytest.fixture
def factory(database: Any) -> QueryFactory:
    return QueryFactory(database, TABLE)


def test_select(factory: QueryFactory, database: Any, recording_dialect: Any) -> None:
    database.select.return_value = SelectQuery(database, recording_dialect, TABLE)

    query = factory.select({"n": "name"})

    assert query is database.select.return_value
    database.select.assert_called_once_with(TABLE)
    query.to_query_with_params()
    assert recording_dialect.last_call[1]["columns"] == (Alias("name", "n"),)


def test_select_defaults_to_every_column(factory: QueryFactory, database: Any, recording_dialect: Any) -> None:
    database.select.return_value = SelectQuery(database, recording_dialect, TABLE)

    factory.select().to_query_with_params()

    assert recording_dialect.last_call[1]["columns"] == ()


def test_insert(factory: QueryFactory, database: Any, recording_dialect: Any) -> None:
    database.insert.return_value = InsertQuery(database, recording_dialect, TABLE)

    factory.insert({"name": "a"}, {"name": "b"}).to_query_with_params()

    database.insert.assert_called_once_with(TABLE)
    assert recording_dialect.last_call[1]["values"] == ({"name": "a"}, {"name": "b"})


def test_update(factory: QueryFactory, database: Any, recording_dialect: Any) -> None:
    database.update.return_value = UpdateQuery(database, recording_dialect, TABLE)

    factory.update({"name": "a"}).to_query_with_params()

    database.update.assert_called_once_with(TABLE)
    assert recording_dialect.last_call[1]["updates"] == {"name": "a"}


@pytest.mark.parametrize(
    ("method", "database_method", "builder"),
    [
        ("delete", "delete", DeleteQuery),
        ("create", "create_table", CreateTableQuery),
        ("alter", "alter_table", AlterTableQuery),
        ("drop", "drop_table", DropTableQuery),
    ],
)
def test_table_builders(
    method: str, database_method: str, builder: type[Any], factory: QueryFactory, database: Any
) -> None:
    getattr(database, database_method).return_value = sentinel = builder(database, database.dialect, TABLE)

    assert getattr(factory, method)() is sentinel
    getattr(database, database_method).assert_called_once_with(TABLE)


@pytest.mark.parametrize(("method", "builder"), [("create_index", CreateIndexQuery), ("drop_index", DropIndexQuery)])
def test_index_builders(method: str, builder: type[Any], factory: QueryFactory, database: Any) -> None:
    getattr(database, method).return_value = sentinel = builder(database, database.dialect, TABLE, "ix")

    assert getattr(factory, method)("ix") is sentinel
    getattr(database, method).assert_called_once_with(TABLE, "ix")


@pytest.mark.parametrize(
    "method", ["columns", "primary_keys", "unique_constraints", "foreign_key_constraints", "indexes"]
)
@pytest.mark.parametrize(
    ("table", "name"), [("users", "users"), (["app", "users"], "users"), (["app", Raw("users")], "users")]
)
def test_introspection_uses_the_table_name(method: str, table: IdentifierLike, name: str, database: Any) -> None:
    introspect = getattr(database, f"information_schema_{method}")
    introspect.return_value = sentinel = [object()]

    assert getattr(QueryFactory(database, table), method)() is sentinel
    introspect.assert_called_once_with(name)
