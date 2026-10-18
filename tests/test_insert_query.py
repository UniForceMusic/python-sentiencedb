"""``InsertQuery``: handoff, native execution and the emulated upsert / ``RETURNING`` flows."""

from __future__ import annotations

from typing import Any

import pytest

from sentiencedb.exceptions import QueryError
from sentiencedb.queries import InsertQuery
from sentiencedb.queries.enums import ConditionEnum, OrderByDirectionEnum
from sentiencedb.queries.objects.clauses import Condition, OnConflict, OrderBy
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.results.result import Result

INSERT, SELECT, UPDATE = QueryWithParams("INSERT"), QueryWithParams("SELECT"), QueryWithParams("UPDATE")


@pytest.fixture
def insert(mock_database: Any, recording_dialect: Any) -> InsertQuery:
    return InsertQuery(mock_database, recording_dialect, "users")


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
        ("on_conflict_do_nothing", (["email"],), OnConflict(["email"])),
        ("on_conflict_do_nothing", ("users_email_key",), OnConflict("users_email_key")),
        ("insert_ignore", (["email"],), OnConflict(["email"])),
        ("on_conflict_do_update", (["email"],), OnConflict(["email"], {})),
        ("on_conflict_do_update", (["email"], {"n": 1}), OnConflict(["email"], {"n": 1})),
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
    assert recording_dialect.last_call[1]["on_conflict"] == OnConflict(["email"], {})
    mock_database.last_insert_id.assert_not_called()


def test_emulated_returning_selects_the_inserted_row(
    insert: InsertQuery, mock_database: Any, legacy_dialect: Any
) -> None:
    selected = Result({"id": None, "name": None}, [{"id": 7, "name": "a"}])
    mock_database.query_with_params.side_effect = [Result(), selected]
    mock_database.last_insert_id.return_value = 7

    insert.values({"name": "a"}).returning(["name"]).last_insert_id("id")

    assert insert.execute(emulate_prepare=True) is selected
    assert _calls(legacy_dialect) == ["insert", "select"]
    select = legacy_dialect.calls[1][1]
    assert select["columns"] == ("id", "name")
    assert select["where"] == (Condition(ConditionEnum.EQUALS, "id", 7),)
    assert select["order_by"] == (OrderBy("id", OrderByDirectionEnum.DESC),)
    assert select["limit"] == 1
    assert [call.args for call in mock_database.query_with_params.call_args_list] == [(INSERT, True), (SELECT, True)]


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
    mock_database.query_with_params.return_value = result = Result()

    assert insert.values({"name": "a"}).returning().execute() is result
    mock_database.last_insert_id.assert_not_called()


def test_emulated_upsert_inserts_a_missing_row(insert: InsertQuery, mock_database: Any, legacy_dialect: Any) -> None:
    mock_database.query_with_params.side_effect = [Result({"email": None}, []), Result()]

    result = insert.values({"email": "a", "name": "b"}).on_conflict_do_update(["email"]).execute(emulate_prepare=True)

    assert (result.columns(), result.fetch_dicts()) == ({}, [])
    assert _calls(legacy_dialect) == ["select", "insert"]
    select, row_insert = legacy_dialect.calls[0][1], legacy_dialect.calls[1][1]
    assert select["where"] == (Condition(ConditionEnum.EQUALS, "email", "a"),)
    assert select["limit"] == 2
    assert row_insert["values"] == ({"email": "a", "name": "b"},)
    assert row_insert["on_conflict"] == OnConflict(["email"], {})
    assert [call.args[1] for call in mock_database.query_with_params.call_args_list] == [True, True]


@pytest.mark.parametrize(("updates", "expected"), [(None, {"email": "a", "name": "b"}), ({"name": "c"}, {"name": "c"})])
def test_emulated_upsert_updates_an_existing_row(
    updates: dict[str, Any] | None,
    expected: dict[str, Any],
    insert: InsertQuery,
    mock_database: Any,
    recording_dialect: Any,
) -> None:
    mock_database.query_with_params.side_effect = [Result({"id": None}, [{"id": 1}]), Result()]

    insert.values({"email": "a", "name": "b"}).on_conflict_do_update(["email"], updates).emulate_on_conflict("id")
    insert.execute()

    assert _calls(recording_dialect) == ["select", "update"]
    assert recording_dialect.calls[0][1]["order_by"] == (OrderBy("id", OrderByDirectionEnum.DESC),)
    assert recording_dialect.last_call[1] == {
        "table": "users",
        "updates": expected,
        "where": (Condition(ConditionEnum.EQUALS, "email", "a"),),
        "returning": None,
    }


def test_emulated_upsert_returns_the_updated_row(insert: InsertQuery, mock_database: Any, legacy_dialect: Any) -> None:
    selected = Result({"id": None, "name": None}, [{"id": 1, "name": "b"}])
    mock_database.query_with_params.side_effect = [Result({"id": None}, [{"id": 1}]), Result(), selected]

    insert.values({"email": "a", "name": "b"}).on_conflict_do_update(["email"]).returning(["name"]).last_insert_id("id")
    result = insert.execute()

    assert result.fetch_dicts() == [{"id": 1, "name": "b"}]
    assert _calls(legacy_dialect) == ["select", "update", "select"]
    assert legacy_dialect.calls[1][1]["returning"] == ("name",)
    assert legacy_dialect.calls[2][1]["where"] == (Condition(ConditionEnum.EQUALS, "email", "a"),)
    assert legacy_dialect.calls[2][1]["limit"] == 1


@pytest.mark.parametrize(("returning", "expected"), [(None, ({}, [])), (["id"], ({"id": "INTEGER"}, [{"id": 1}]))])
def test_emulated_do_nothing_skips_an_existing_row(
    returning: list[str] | None,
    expected: tuple[dict[str, Any], list[dict[str, Any]]],
    insert: InsertQuery,
    mock_database: Any,
    legacy_dialect: Any,
) -> None:
    mock_database.query_with_params.return_value = Result({"id": "INTEGER"}, [{"id": 1}])
    insert.values({"email": "a"}).on_conflict_do_nothing(["email"])

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
        Result({"id": None}, [{"id": 2}]),
    ]
    mock_database.last_insert_id.return_value = 2

    insert.values({"email": "a"}, {"email": "b"}).on_conflict_do_nothing(["email"]).returning(["id"]).last_insert_id(
        "id"
    )
    result = insert.execute()

    assert isinstance(result, Result)
    assert (result.columns(), result.fetch_dicts()) == ({"id": None}, [{"id": 1}, {"id": 2}])
    assert _calls(legacy_dialect) == ["select", "select", "insert", "select"]


def test_emulated_upsert_in_transaction(insert: InsertQuery, mock_database: Any, recording_dialect: Any) -> None:
    mock_database.transaction.side_effect = lambda callback, *args: callback(mock_database)
    mock_database.query_with_params.side_effect = [Result({}, []), Result()]

    insert.values({"email": "a"}).on_conflict_do_nothing(["email"]).emulate_on_conflict("id", in_transaction=True)
    insert.execute()

    mock_database.transaction.assert_called_once()
    assert _calls(recording_dialect) == ["select", "insert"]


@pytest.mark.parametrize(
    ("conflict", "rows", "message"),
    [
        ("users_email_key", [], "database does not support named constraints"),
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
