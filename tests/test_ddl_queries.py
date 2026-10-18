"""DDL builders: handoff, column helpers and emulated ``IF [NOT] EXISTS``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from sentiencedb.queries import (
    AlterTableQuery,
    CreateIndexQuery,
    CreateTableQuery,
    DropIndexQuery,
    DropTableQuery,
)
from sentiencedb.queries.enums import ConditionEnum, ReferentialActionEnum, TypeEnum
from sentiencedb.queries.objects.clauses import Condition
from sentiencedb.queries.objects.ddl import (
    AddColumn,
    AddForeignKeyConstraint,
    AddPrimaryKeys,
    AddUniqueConstraint,
    AlterColumn,
    Column,
    DropColumn,
    DropConstraint,
    ForeignKeyConstraint,
    Index,
    RenameColumn,
    Type,
    UniqueConstraint,
)
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.queries.objects.sql import CurrentTimestamp, Raw
from sentiencedb.results.result import Result


@pytest.fixture
def legacy_dialect(recording_dialect: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The recording dialect without ``IF [NOT] EXISTS`` support."""
    for feature in ("table_exists", "index_exists"):
        monkeypatch.setattr(type(recording_dialect), feature, property(lambda self: False))

    return recording_dialect


# -- CREATE TABLE ----------------------------------------------------------------------------------------------


def test_create_table_defaults(mock_database: Any, recording_dialect: Any) -> None:
    CreateTableQuery(mock_database, recording_dialect, "t").to_query_with_params()

    assert recording_dialect.last_call == (
        "create_table",
        {"table": "t", "columns": (), "primary_keys": (), "constraints": (), "if_not_exists": False},
    )


def test_create_table_columns(mock_database: Any, recording_dialect: Any) -> None:
    now = datetime(2024, 1, 2, 3, 4, 5)
    query = CreateTableQuery(mock_database, recording_dialect, ["app", "t"])

    returned = (
        query.identity("id")
        .auto_increment("seq", 32, primary_key=False)
        .boolean("active", True, False)
        .integer("count", 32, default=0)
        .float("price", not_null=True, default=Decimal("1.5"))
        .varchar("name", 100, default="x")
        .text("bio", 70000)
        .datetime("created", 3, True, CurrentTimestamp())
        .datetime("seen", default=now)
        .column("raw", "JSONB", True, Raw("'{}'"))
        .column("typed", Type(TypeEnum.INTEGER, 16), generated_by_default_as_identity=True)
        .if_not_exists()
    )

    assert returned is query
    query.to_query_with_params()
    assert recording_dialect.last_call[1] == {
        "table": ["app", "t"],
        "columns": (
            Column("id", Type(TypeEnum.INTEGER, 64), True, None, True),
            Column("seq", Type(TypeEnum.INTEGER, 32), True, None, True),
            Column("active", Type(TypeEnum.BOOLEAN), True, False),
            Column("count", Type(TypeEnum.INTEGER, 32), False, 0),
            Column("price", Type(TypeEnum.FLOAT, 64), True, Decimal("1.5")),
            Column("name", Type(TypeEnum.VARCHAR, 100), False, "x"),
            Column("bio", Type(TypeEnum.TEXT, 70000)),
            Column("created", Type(TypeEnum.DATETIME, 3), True, CurrentTimestamp()),
            Column("seen", Type(TypeEnum.DATETIME, 6), False, now),
            Column("raw", "JSONB", True, Raw("'{}'")),
            Column("typed", Type(TypeEnum.INTEGER, 16), False, None, True),
        ),
        "primary_keys": ("id",),
        "constraints": (),
        "if_not_exists": True,
    }


def test_create_table_primary_keys_and_constraints(mock_database: Any, recording_dialect: Any) -> None:
    query = (
        CreateTableQuery(mock_database, recording_dialect, "t")
        .identity("id")
        .identity("id")
        .primary_keys(["a", "b"])
        .identity("c")
        .unique_constraint(["a", "b"])
        .unique_constraint(["c"], "uq_c")
        .foreign_key_constraint("a", ["app", "other"], "id")
        .foreign_key_constraint(["a", "b"], "other", ["x", "y"], "fk", ReferentialActionEnum.CASCADE, "SET DEFAULT")
        .constraint("CHECK (a > 0)")
    )

    query.to_query_with_params()
    kwargs = recording_dialect.last_call[1]

    assert kwargs["primary_keys"] == ("a", "b", "c")
    assert kwargs["constraints"] == (
        UniqueConstraint(("a", "b")),
        UniqueConstraint(("c",), "uq_c"),
        ForeignKeyConstraint(("a",), ["app", "other"], ("id",)),
        ForeignKeyConstraint(("a", "b"), "other", ("x", "y"), "fk", ReferentialActionEnum.CASCADE, "SET DEFAULT"),
        Raw("CHECK (a > 0)"),
    )


def test_create_table_primary_keys_accept_one_column(mock_database: Any, recording_dialect: Any) -> None:
    CreateTableQuery(mock_database, recording_dialect, "t").primary_keys("id").to_query_with_params()

    assert recording_dialect.last_call[1]["primary_keys"] == ("id",)


@pytest.mark.parametrize("if_not_exists", [False, True])
def test_create_table_native(if_not_exists: bool, mock_database: Any, recording_dialect: Any) -> None:
    mock_database.query_with_params.return_value = result = Result()
    query = CreateTableQuery(mock_database, recording_dialect, "t").varchar("a")

    if if_not_exists:
        query.if_not_exists()

    assert query.execute(True) is result
    assert recording_dialect.last_call[1]["if_not_exists"] is if_not_exists
    mock_database.query_with_params.assert_called_once_with(QueryWithParams("CREATE_TABLE"), True)
    mock_database.information_schema_tables.assert_not_called()


@pytest.mark.parametrize(("tables", "executed"), [(["t", "other"], False), (["other"], True)])
def test_create_table_emulated(tables: list[str], executed: bool, mock_database: Any, recording_dialect: Any) -> None:
    mock_database.information_schema_tables.return_value = tables
    mock_database.query_with_params.return_value = Result()

    query = CreateTableQuery(mock_database, recording_dialect, ["app", "t"]).varchar("a").if_not_exists()
    result = query.emulate_if_not_exists().execute()

    assert result.columns() == {}
    assert mock_database.query_with_params.called is executed
    assert [kwargs["if_not_exists"] for _, kwargs in recording_dialect.calls] == [False] * executed


def test_create_table_emulated_when_unsupported(mock_database: Any, legacy_dialect: Any) -> None:
    mock_database.information_schema_tables.return_value = ["t"]

    CreateTableQuery(mock_database, legacy_dialect, Raw("t")).varchar("a").if_not_exists().execute()

    mock_database.query_with_params.assert_not_called()


def test_emulate_if_not_exists_alone_does_nothing(mock_database: Any, recording_dialect: Any) -> None:
    CreateTableQuery(mock_database, recording_dialect, "t").varchar("a").emulate_if_not_exists().execute()

    mock_database.information_schema_tables.assert_not_called()
    mock_database.query_with_params.assert_called_once()


# -- ALTER TABLE -----------------------------------------------------------------------------------------------


def test_alter_table(mock_database: Any, recording_dialect: Any) -> None:
    query = AlterTableQuery(mock_database, recording_dialect, "t")

    returned = (
        query.add_column("a", "TEXT", True, "x")
        .add_identity("id", 32)
        .add_auto_increment("seq", add_primary_key=False)
        .add_boolean("b", default=True)
        .add_integer("i", 16, True, 0)
        .add_float("f", 32, default=1.5)
        .add_varchar("v", 10)
        .add_text("x")
        .add_datetime("d", 0, default=CurrentTimestamp())
        .alter_column("a", "TYPE VARCHAR(10)")
        .rename_column("a", "z")
        .drop_column("z")
        .add_primary_keys(["i", "f"])
        .add_unique_constraint(["v"], "uq_v")
        .add_foreign_key_constraint("i", "other", "id", on_delete=ReferentialActionEnum.SET_NULL)
        .drop_constraint("uq_v")
        .alter("OWNER TO admin")
    )

    assert returned is query
    assert query.to_query_with_params() == [QueryWithParams("ALTER_TABLE")]
    assert recording_dialect.last_call == (
        "alter_table",
        {
            "table": "t",
            "alters": (
                AddColumn("a", "TEXT", True, "x"),
                AddColumn("id", Type(TypeEnum.INTEGER, 32), True, None, True),
                AddPrimaryKeys(("id",)),
                AddColumn("seq", Type(TypeEnum.INTEGER, 64), True, None, True),
                AddColumn("b", Type(TypeEnum.BOOLEAN), False, True),
                AddColumn("i", Type(TypeEnum.INTEGER, 16), True, 0),
                AddColumn("f", Type(TypeEnum.FLOAT, 32), False, 1.5),
                AddColumn("v", Type(TypeEnum.VARCHAR, 10)),
                AddColumn("x", Type(TypeEnum.TEXT)),
                AddColumn("d", Type(TypeEnum.DATETIME, 0), False, CurrentTimestamp()),
                AlterColumn("a", "TYPE VARCHAR(10)"),
                RenameColumn("a", "z"),
                DropColumn("z"),
                AddPrimaryKeys(("i", "f")),
                AddUniqueConstraint(("v",), "uq_v"),
                AddForeignKeyConstraint(("i",), "other", ("id",), on_delete=ReferentialActionEnum.SET_NULL),
                DropConstraint("uq_v"),
                Raw("OWNER TO admin"),
            ),
        },
    )


def test_alter_table_execute_and_to_sql(mock_database: Any, recording_dialect: Any) -> None:
    mock_database.query_with_params.return_value = result = Result()
    query = AlterTableQuery(mock_database, recording_dialect, "t").drop_column("a")

    assert query.to_sql() == ["ALTER_TABLE"]
    assert query.execute(True) == [result]
    mock_database.query_with_params.assert_called_once_with(QueryWithParams("ALTER_TABLE"), True)


# -- DROP TABLE ------------------------------------------------------------------------------------------------


def test_drop_table(mock_database: Any, recording_dialect: Any) -> None:
    mock_database.query_with_params.return_value = result = Result()
    query = DropTableQuery(mock_database, recording_dialect, "t")

    assert query.execute() is result
    assert recording_dialect.last_call == ("drop_table", {"table": "t", "if_exists": False})

    assert query.if_exists().execute(True) is result
    assert recording_dialect.last_call == ("drop_table", {"table": "t", "if_exists": True})
    mock_database.information_schema_tables.assert_not_called()


@pytest.mark.parametrize(("tables", "executed"), [(["t"], True), (["other"], False)])
def test_drop_table_emulated(tables: list[str], executed: bool, mock_database: Any, legacy_dialect: Any) -> None:
    mock_database.information_schema_tables.return_value = tables

    DropTableQuery(mock_database, legacy_dialect, ["app", "t"]).if_exists().execute()

    assert mock_database.query_with_params.called is executed


def test_drop_table_emulate_if_exists_overrides_native(mock_database: Any, recording_dialect: Any) -> None:
    mock_database.information_schema_tables.return_value = []

    query = DropTableQuery(mock_database, recording_dialect, "t").if_exists().emulate_if_exists()

    assert query.execute().fetch_dicts() == []
    mock_database.query_with_params.assert_not_called()

    query.to_query_with_params()
    assert recording_dialect.last_call[1]["if_exists"] is False


# -- CREATE / DROP INDEX ---------------------------------------------------------------------------------------


def test_create_index(mock_database: Any, recording_dialect: Any) -> None:
    query = CreateIndexQuery(mock_database, recording_dialect, "t", "ix")

    assert query.to_query_with_params() == QueryWithParams("CREATE_INDEX")
    assert recording_dialect.last_call == (
        "create_index",
        {"table": "t", "name": "ix", "columns": (), "unique": False, "if_not_exists": False, "where": ()},
    )

    returned = query.columns(["a"]).columns(["b", "c"]).unique().if_not_exists().where_is_not_null("b")
    returned.to_query_with_params()

    assert returned is query
    assert recording_dialect.last_call[1] == {
        "table": "t",
        "name": "ix",
        "columns": ("b", "c"),
        "unique": True,
        "if_not_exists": True,
        "where": (Condition(ConditionEnum.NOT_EQUALS, "b", None),),
    }


@pytest.mark.parametrize(
    ("indexes", "executed"),
    [
        ({"t": [], "other": [Index("ix", ["a"])]}, False),
        ({"t": [Index("ix", ["a"], unique=True)], "other": []}, False),
        ({"t": [Index("ix_other", ["a"])], "other": []}, True),
    ],
)
def test_create_index_emulated(
    indexes: dict[str, list[Index]], executed: bool, mock_database: Any, legacy_dialect: Any
) -> None:
    mock_database.information_schema_tables.return_value = ["other", "t"]
    mock_database.information_schema_indexes.side_effect = lambda table: indexes[table]

    query = CreateIndexQuery(mock_database, legacy_dialect, ["app", "t"], "ix").columns(["a"]).if_not_exists()
    query.execute()

    assert mock_database.query_with_params.called is executed
    assert mock_database.information_schema_indexes.call_args_list[0].args == ("t",)


@pytest.mark.parametrize("create", [True, False])
def test_mysql_index_names_are_per_table(create: bool, mock_database: Any, mysql_dialect: Any) -> None:
    """An index of the same name on another table neither blocks ``CREATE`` nor satisfies ``DROP``."""
    mock_database.information_schema_indexes.side_effect = lambda table: [Index("ix", ["a"])] * (table == "other")
    query = CreateIndexQuery(mock_database, mysql_dialect, "t", "ix").columns(["a"]).if_not_exists()

    (query if create else DropIndexQuery(mock_database, mysql_dialect, "t", "ix").if_exists()).execute()

    assert mock_database.query_with_params.called is create
    mock_database.information_schema_tables.assert_not_called()
    mock_database.information_schema_indexes.assert_called_once_with("t")


def test_create_index_native(mock_database: Any, recording_dialect: Any) -> None:
    CreateIndexQuery(mock_database, recording_dialect, "t", "ix").columns(["a"]).if_not_exists().execute()

    mock_database.information_schema_indexes.assert_not_called()
    mock_database.query_with_params.assert_called_once_with(QueryWithParams("CREATE_INDEX"), False)


def test_drop_index(mock_database: Any, recording_dialect: Any) -> None:
    query = DropIndexQuery(mock_database, recording_dialect, "t", "ix")

    query.execute()
    assert recording_dialect.last_call == ("drop_index", {"table": "t", "name": "ix", "if_exists": False})

    query.if_exists().execute()
    assert recording_dialect.last_call == ("drop_index", {"table": "t", "name": "ix", "if_exists": True})
    mock_database.information_schema_indexes.assert_not_called()


@pytest.mark.parametrize(("indexes", "executed"), [([Index("ix", ["a"])], True), ([], False)])
def test_drop_index_emulated(indexes: list[Index], executed: bool, mock_database: Any, recording_dialect: Any) -> None:
    mock_database.information_schema_tables.return_value = ["t"]
    mock_database.information_schema_indexes.return_value = indexes

    DropIndexQuery(mock_database, recording_dialect, "t", "ix").if_exists().emulate_if_exists().execute()

    assert mock_database.query_with_params.called is executed
    assert recording_dialect.calls == [("drop_index", {"table": "t", "name": "ix", "if_exists": False})] * executed
