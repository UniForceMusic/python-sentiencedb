"""``InsertQuery``: handoff, native execution and the emulated upsert / ``RETURNING`` flows."""

from __future__ import annotations

from typing import Any

import pytest

from sentiencedb.driver import Driver
from sentiencedb.exceptions import QueryError
from sentiencedb.queries import InsertQuery
from sentiencedb.queries.enums import ConditionEnum
from sentiencedb.queries.objects.clauses import Condition, OnConflict
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.queries.objects.sql import Raw
from sentiencedb.results.result import Result

INSERT, SELECT, UPDATE = QueryWithParams("INSERT"), QueryWithParams("SELECT"), QueryWithParams("UPDATE")


@pytest.fixture
def insert(mock_database: Any, recording_dialect: Any) -> InsertQuery:
    return InsertQuery(mock_database, recording_dialect, "users")


@pytest.fixture(autouse=True)
def _transactions_run_their_callback(mock_database: Any) -> None:
    mock_database.transaction.side_effect = lambda callback, *args, **kwargs: callback(mock_database)


@pytest.fixture
def legacy_dialect(recording_dialect: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The recording dialect without native upserts and ``RETURNING``."""
    for feature in ("on_conflict", "returning"):
        monkeypatch.setattr(type(recording_dialect), feature, property(lambda self: False))

    return recording_dialect


def _kwargs(insert: InsertQuery, recording_dialect: Any) -> dict[str, Any]:
    assert insert.to_query_with_params() == INSERT

    return dict(recording_dialect.last_call[1])


def _calls(recording_dialect: Any) -> list[str]:
    return [method for method, _ in recording_dialect.calls]


def test_defaults(insert: InsertQuery, recording_dialect: Any) -> None:
    assert _kwargs(insert, recording_dialect) == {
        "table": "users",
        "values": (),
        "on_conflict": None,
        "returning": None,
        "last_insert_id": None,
    }


def test_values_append_rows(insert: InsertQuery, recording_dialect: Any) -> None:
    returned = insert.values({"name": "a"}).values({"name": "b"}, {"name": "c", "age": 3})

    assert returned is insert
    assert _kwargs(insert, recording_dialect)["values"] == ({"name": "a"}, {"name": "b"}, {"name": "c", "age": 3})


@pytest.mark.parametrize(
    ("method", "args", "expected"),
    [
        ("on_conflict_do_nothing", (["email"],), OnConflict(("email",))),
        ("on_conflict_do_nothing", ("users_email_key",), OnConflict("users_email_key")),
        ("insert_ignore", (["email"],), OnConflict(("email",))),
        ("on_conflict_do_update", (["email"],), OnConflict(("email",), {})),
        ("on_conflict_do_update", (["email"], {"n": 1}), OnConflict(("email",), {"n": 1})),
        ("on_duplicate_key_update", ("users_email_key",), OnConflict("users_email_key", {})),
    ],
)
def test_on_conflict(
    method: str, args: tuple[Any, ...], expected: OnConflict, insert: InsertQuery, recording_dialect: Any
) -> None:
    assert getattr(insert, method)(*args) is insert
    assert _kwargs(insert, recording_dialect)["on_conflict"] == expected


def test_returning_and_last_insert_id(insert: InsertQuery, recording_dialect: Any) -> None:
    assert _kwargs(insert.returning(), recording_dialect)["returning"] == ()

    kwargs = _kwargs(insert.returning(["id", "name"]).last_insert_id("id"), recording_dialect)

    assert (kwargs["returning"], kwargs["last_insert_id"]) == (("id", "name"), "id")


def test_emulation_hides_clauses_from_the_dialect(insert: InsertQuery, recording_dialect: Any) -> None:
    insert.on_conflict_do_nothing(["email"]).returning().emulate_on_conflict("id").emulate_returning("pk")

    kwargs = _kwargs(insert, recording_dialect)

    assert (kwargs["on_conflict"], kwargs["returning"], kwargs["last_insert_id"]) == (None, None, "pk")


@pytest.mark.parametrize("emulate_prepare", [False, True])
def test_plain_insert(emulate_prepare: bool, insert: InsertQuery, mock_database: Any) -> None:
    mock_database.query_with_params.return_value = result = Result()

    assert insert.values({"name": "a"}).execute(emulate_prepare) is result

    mock_database.query_with_params.assert_called_once_with(INSERT, emulate_prepare)
    mock_database.last_insert_id.assert_not_called()


def test_native_upsert_and_returning_run_one_statement(
    insert: InsertQuery, mock_database: Any, recording_dialect: Any
) -> None:
    mock_database.query_with_params.return_value = result = Result({"id": None}, [{"id": 1}])

    insert.values({"email": "a"}).on_conflict_do_update(["email"]).returning(["id"]).last_insert_id("id")

    assert insert.execute() is result
    assert _calls(recording_dialect) == ["insert"]
    assert recording_dialect.last_call[1]["on_conflict"] == OnConflict(("email",), {})
    mock_database.last_insert_id.assert_not_called()


def _where(value: Any, column: str = "id") -> tuple[Condition, ...]:
    return (Condition(ConditionEnum.EQUALS, column, value),)


def test_emulated_returning_selects_the_inserted_row(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    selected = Result({"name": None}, [{"name": "a"}])
    mock_database.query_with_params.side_effect = [Result(), selected]
    mock_database.last_insert_id.return_value = 7

    insert.values({"name": "a"}).returning(["name"]).last_insert_id("id")

    assert insert.execute(emulate_prepare=True) is selected
    assert _calls(legacy_dialect) == ["insert", "select"]
    select = legacy_dialect.calls[1][1]
    assert (select["columns"], select["where"], select["order_by"], select["limit"]) == (("name",), _where(7), (), 1)
    assert [call.args for call in mock_database.query_with_params.call_args_list] == [(INSERT, True), (SELECT, True)]


@pytest.mark.parametrize("explicit_id", [100, "uuid-1"])
def test_emulated_returning_selects_an_explicit_id_back(
    explicit_id: Any, insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    """``last_insert_id()`` does not cover a row that supplies its id; the row's own value identifies it."""
    mock_database.query_with_params.side_effect = [Result(), Result()]

    insert.values({"id": explicit_id, "name": "a"}).returning(["name"]).last_insert_id("id").execute()

    assert legacy_dialect.calls[1][1]["where"] == _where(explicit_id)
    mock_database.last_insert_id.assert_not_called()


def test_emulated_returning_falls_back_to_last_insert_id_for_a_sql_id(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    mock_database.query_with_params.side_effect = [Result(), Result()]
    mock_database.last_insert_id.return_value = 4

    insert.values({"id": Raw("nextval('s')")}).returning().last_insert_id("id").execute()

    assert legacy_dialect.calls[1][1]["where"] == _where(4)


def test_emulated_returning_inserts_several_rows_one_by_one(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    """The last insert id identifies a single row (on MySQL the first of a multi-row insert)."""
    mock_database.query_with_params.side_effect = [
        Result(),
        Result({"id": None}, [{"id": 1}]),
        Result(),
        Result({"id": None}, [{"id": 2}]),
    ]
    mock_database.last_insert_id.side_effect = [1, 2]

    result = insert.values({"name": "a"}, {"name": "b"}).returning(["id"]).last_insert_id("id").execute()

    assert result.fetch_dicts() == [{"id": 1}, {"id": 2}]
    assert _calls(legacy_dialect) == ["insert", "select", "insert", "select"]
    assert [call[1]["values"] for call in legacy_dialect.calls[::2]] == [({"name": "a"},), ({"name": "b"},)]
    mock_database.transaction.assert_called_once()


def test_emulate_returning_overrides_native_support(
    insert: InsertQuery, mock_database: Any, recording_dialect: Any
) -> None:
    mock_database.query_with_params.side_effect = [Result(), Result()]
    mock_database.last_insert_id.return_value = 3

    insert.values({"name": "a"}).returning().emulate_returning("id").execute()

    assert _calls(recording_dialect) == ["insert", "select"]
    assert recording_dialect.calls[0][1]["returning"] is None
    assert recording_dialect.calls[1][1]["columns"] == ()


@pytest.mark.parametrize("last_insert_id", [None, 0])
def test_emulated_returning_without_an_id_returns_the_insert_result(
    last_insert_id: int | None, insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    mock_database.query_with_params.return_value = result = Result()
    mock_database.last_insert_id.return_value = last_insert_id

    assert insert.values({"name": "a"}).returning().last_insert_id("id").execute() is result
    assert _calls(legacy_dialect) == ["insert"]


def test_returning_without_last_insert_id_cannot_be_emulated(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    with pytest.raises(QueryError, match="needs last_insert_id"):
        insert.values({"name": "a"}).returning().execute()

    mock_database.query_with_params.assert_not_called()


@pytest.mark.parametrize("emulate", [False, True])
def test_no_values_raise(emulate: bool, insert: InsertQuery, mock_database: Any) -> None:
    insert.on_conflict_do_nothing(["email"])

    if emulate:
        insert.emulate_on_conflict("id")

    with pytest.raises(QueryError, match="no insert values"):
        insert.execute()

    mock_database.query_with_params.assert_not_called()


def test_emulated_upsert_inserts_a_missing_row(insert: InsertQuery, mock_database: Any, legacy_dialect: Any) -> None:
    mock_database.query_with_params.side_effect = [Result({"email": None}, []), Result()]

    result = insert.values({"email": "a", "name": "b"}).on_conflict_do_update(["email"]).execute(emulate_prepare=True)

    assert (result.columns(), result.fetch_dicts()) == ({}, [])
    assert _calls(legacy_dialect) == ["select", "insert"]
    select, row_insert = legacy_dialect.calls[0][1], legacy_dialect.calls[1][1]
    assert (select["columns"], select["where"], select["limit"]) == (("email",), _where("a", "email"), 2)
    assert row_insert["values"] == ({"email": "a", "name": "b"},)
    assert row_insert["on_conflict"] is None
    assert [call.args[1] for call in mock_database.query_with_params.call_args_list] == [True, True]
    mock_database.transaction.assert_called_once()


@pytest.mark.parametrize(("updates", "expected"), [(None, {"email": "a", "name": "b"}), ({"name": "c"}, {"name": "c"})])
def test_emulated_upsert_updates_the_existing_row_by_its_id(
    updates: dict[str, Any] | None,
    expected: dict[str, Any],
    insert: InsertQuery,
    mock_database: Any,
    recording_dialect: Any,
) -> None:
    mock_database.query_with_params.side_effect = [Result({"id": None}, [{"id": 9}]), Result()]

    insert.values({"email": "a", "name": "b"}).on_conflict_do_update(["email"], updates).emulate_on_conflict("id")
    insert.execute()

    assert _calls(recording_dialect) == ["select", "update"]
    assert recording_dialect.calls[0][1]["columns"] == ("id",)
    assert recording_dialect.last_call[1] == {
        "table": "users",
        "updates": expected,
        "where": _where(9),
        "returning": None,
    }


def test_emulated_upsert_returns_the_updated_row(insert: InsertQuery, mock_database: Any, legacy_dialect: Any) -> None:
    selected = Result({"name": None}, [{"name": "b"}])
    mock_database.query_with_params.side_effect = [Result({"id": None}, [{"id": 1}]), Result(), selected]

    insert.values({"email": "a", "name": "b"}).on_conflict_do_update(["email"]).returning(["name"]).last_insert_id("id")
    result = insert.execute()

    assert result.fetch_dicts() == [{"name": "b"}]
    assert _calls(legacy_dialect) == ["select", "update", "select"]
    assert legacy_dialect.calls[1][1]["returning"] is None
    select = legacy_dialect.calls[2][1]
    assert (select["columns"], select["where"], select["limit"]) == (("name",), _where(1), 1)


def test_emulated_upsert_without_an_id_follows_changed_conflict_values(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    """An update that changes the conflict columns is selected back by their new values."""
    mock_database.query_with_params.side_effect = [Result({"email": None}, [{"email": "a"}]), Result(), Result()]

    insert.values({"email": "a", "name": "b"}).on_conflict_do_update(["email"], {"email": "z"}).returning(["name"])
    insert.execute()

    assert legacy_dialect.calls[1][1]["where"] == _where("a", "email")
    assert legacy_dialect.calls[2][1]["where"] == _where("z", "email")


@pytest.mark.parametrize(
    ("update_returning", "calls", "update_kwarg"),
    [(True, ["select", "update"], ("name",)), (False, ["select", "update", "select"], None)],
)
def test_emulated_upsert_update_path_follows_update_returning(
    update_returning: bool,
    calls: list[str],
    update_kwarg: tuple[str, ...] | None,
    insert: InsertQuery,
    mock_database: Any,
    recording_dialect: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MariaDB has ``INSERT .. RETURNING`` but no ``UPDATE .. RETURNING``: the update path selects the row back."""
    monkeypatch.setattr(type(recording_dialect), "update_returning", property(lambda self: update_returning))
    updated = Result({"name": None}, [{"name": "b"}])
    mock_database.query_with_params.side_effect = [Result({"id": None}, [{"id": 1}]), updated, updated]

    insert.values({"email": "a", "name": "b"}).on_conflict_do_update(["email"]).returning(["name"])
    result = insert.emulate_on_conflict("id").execute()

    assert result.fetch_dicts() == [{"name": "b"}]
    assert _calls(recording_dialect) == calls
    assert recording_dialect.calls[1][1]["returning"] == update_kwarg


@pytest.mark.parametrize(("returning", "expected"), [(None, ({}, [])), (["id"], ({"id": None}, []))])
def test_emulated_do_nothing_returns_no_row_for_a_skipped_row(
    returning: list[str] | None,
    expected: tuple[dict[str, Any], list[dict[str, Any]]],
    insert: InsertQuery,
    mock_database: Any,
    legacy_dialect: Any,
) -> None:
    mock_database.query_with_params.return_value = Result({"id": "INTEGER"}, [{"id": 1}])
    insert.values({"email": "a"}).on_conflict_do_nothing(["email"]).last_insert_id("id")

    if returning is not None:
        insert.returning(returning)

    result = insert.execute()

    assert (result.columns(), result.fetch_dicts()) == expected
    assert _calls(legacy_dialect) == ["select"]


def test_emulated_upsert_merges_rows_into_one_result(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    mock_database.query_with_params.side_effect = [
        Result({"id": None}, [{"id": 1}]),
        Result({"id": None}, []),
        Result(),
        Result({"id": "INTEGER"}, [{"id": 2}]),
    ]

    query = insert.values({"email": "a"}, {"email": "b"}).on_conflict_do_nothing(["email"]).returning(["id"])
    result = query.last_insert_id("id").execute()

    assert isinstance(result, Result)
    assert (result.columns(), result.fetch_dicts()) == ({"id": "INTEGER"}, [{"id": 2}])
    assert _calls(legacy_dialect) == ["select", "select", "insert", "select"]
    assert legacy_dialect.calls[3][1]["where"] == _where("b", "email")
    mock_database.last_insert_id.assert_not_called()
    mock_database.transaction.assert_called_once()


def test_emulated_upsert_inserts_a_row_with_a_null_conflict_value(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    """A unique key never matches ``NULL``: the row is inserted without looking for an existing row."""
    mock_database.query_with_params.side_effect = [Result(), Result({"id": None}, [{"id": 5}])]
    mock_database.last_insert_id.return_value = 5

    insert.values({"email": None, "name": "x"}).on_conflict_do_update(["email"]).returning(["id"]).last_insert_id("id")

    assert insert.execute().fetch_dicts() == [{"id": 5}]
    assert _calls(legacy_dialect) == ["insert", "select"]
    assert legacy_dialect.calls[1][1]["where"] == _where(5)


def test_emulated_returning_routes_a_native_upsert_through_the_emulation(
    insert: InsertQuery, mock_database: Any, recording_dialect: Any
) -> None:
    """A native upsert does not say which row it touched, so emulated RETURNING emulates the upsert too."""
    mock_database.query_with_params.side_effect = [Result({"id": None}, [{"id": 1}]), Result(), Result()]

    insert.values({"email": "a"}).on_conflict_do_update(["email"]).returning(["email"]).emulate_returning("id")
    insert.execute()

    assert _calls(recording_dialect) == ["select", "update", "select"]


@pytest.mark.parametrize("driver", [Driver.MYSQL, Driver.MARIADB])
def test_mysql_do_nothing_with_returning_is_emulated(
    driver: Driver, insert: InsertQuery, mock_database: Any, recording_dialect: Any
) -> None:
    """``ON DUPLICATE KEY UPDATE .. RETURNING`` would return the skipped row."""
    recording_dialect.driver = driver
    mock_database.query_with_params.side_effect = [Result({"id": None}, [{"id": 1}])]

    result = insert.values({"email": "a"}).on_conflict_do_nothing(["email"]).returning(["id"]).execute()

    assert result.fetch_dicts() == []
    assert _calls(recording_dialect) == ["select"]


@pytest.mark.parametrize(
    ("driver", "on_conflict", "calls"),
    [(Driver.SQLITE, None, ["insert"]), (Driver.MARIADB, {}, ["insert"]), (Driver.MYSQL, None, ["insert"])],
)
def test_other_native_upserts_stay_native(
    driver: Driver,
    on_conflict: dict[str, Any] | None,
    calls: list[str],
    insert: InsertQuery,
    mock_database: Any,
    recording_dialect: Any,
) -> None:
    recording_dialect.driver = driver
    mock_database.query_with_params.return_value = Result()
    insert.values({"email": "a"})

    if on_conflict is None:
        insert.on_conflict_do_nothing(["email"])
    else:
        insert.on_conflict_do_update(["email"], on_conflict).returning(["id"])

    insert.execute()

    assert _calls(recording_dialect) == calls
    assert recording_dialect.last_call[1]["on_conflict"] is not None
    mock_database.transaction.assert_not_called()


@pytest.mark.parametrize(
    ("conflict", "rows", "message"),
    [
        ("users_email_key", [], "needs conflict columns, not the constraint name 'users_email_key'"),
        ([], [], "at least one conflict column"),
        (["email", "tenant"], [], "insert values does not contain constraint columns"),
        (["email"], [{"id": 1}, {"id": 2}], "multiple rows in constraint"),
    ],
)
def test_emulated_upsert_errors(
    conflict: str | list[str],
    rows: list[dict[str, Any]],
    message: str,
    insert: InsertQuery,
    mock_database: Any,
    legacy_dialect: Any,
) -> None:
    mock_database.query_with_params.return_value = Result({"id": None}, rows)

    with pytest.raises(QueryError, match=message):
        insert.values({"email": "a"}).on_conflict_do_update(conflict).execute()


def test_emulated_upsert_validates_every_row_before_writing(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    insert.values({"email": "a"}, {"name": "no email"}).on_conflict_do_nothing(["email"])

    with pytest.raises(QueryError, match="constraint columns"):
        insert.execute()

    mock_database.query_with_params.assert_not_called()


def test_emulated_upsert_needs_an_id_for_null_conflict_rows_with_returning(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    insert.values({"email": "a"}, {"email": None}).on_conflict_do_nothing(["email"]).returning()

    with pytest.raises(QueryError, match="needs last_insert_id"):
        insert.execute()

    mock_database.query_with_params.assert_not_called()


def test_emulated_upsert_finds_rows_by_conflict_columns_without_an_id(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    mock_database.query_with_params.side_effect = [Result(), Result(), Result({"name": None}, [{"name": "n"}])]

    result = insert.values({"email": "a", "name": "n"}).on_conflict_do_nothing(["email"]).returning(["name"]).execute()

    assert result.fetch_dicts() == [{"name": "n"}]
    assert legacy_dialect.calls[2][1]["where"] == _where("a", "email")


def test_builders_copy_the_callers_containers(insert: InsertQuery, recording_dialect: Any) -> None:
    row: dict[str, Any] = {"email": "a"}
    conflict = ["email"]
    updates: dict[str, Any] = {"name": "b"}
    insert.values(row).on_conflict_do_update(conflict, updates)
    row["email"], updates["name"] = "changed", "changed"
    conflict.append("changed")

    kwargs = _kwargs(insert, recording_dialect)

    assert kwargs["values"] == ({"email": "a"},)
    assert kwargs["on_conflict"] == OnConflict(("email",), {"name": "b"})
