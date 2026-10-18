"""``UpdateQuery`` and ``DeleteQuery`` handoff and execution."""

from __future__ import annotations

from typing import Any

from sentiencedb.queries import DeleteQuery, SelectQuery, UpdateQuery
from sentiencedb.queries.enums import ChainEnum, ConditionEnum
from sentiencedb.queries.objects.clauses import Condition
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.queries.objects.sql import Raw
from sentiencedb.results.result import Result


def test_update_defaults(mock_database: Any, recording_dialect: Any) -> None:
    UpdateQuery(mock_database, recording_dialect, "users").to_query_with_params()

    assert recording_dialect.last_call == ("update", {"table": "users", "updates": {}, "where": (), "returning": None})


def test_update(mock_database: Any, recording_dialect: Any) -> None:
    sub_select = SelectQuery(mock_database, recording_dialect, "teams")
    update = UpdateQuery(mock_database, recording_dialect, ["app", "users"])

    returned = (
        update.set({"ignored": 1})
        .set({"name": "a", "seen": Raw("now()"), "team": sub_select})
        .where_equals("id", 1)
        .or_where_is_null("name")
        .returning(["id"])
    )

    assert returned is update
    assert update.to_query_with_params() == QueryWithParams("UPDATE")
    assert recording_dialect.last_call == (
        "update",
        {
            "table": ["app", "users"],
            "updates": {"name": "a", "seen": Raw("now()"), "team": sub_select},
            "where": (
                Condition(ConditionEnum.EQUALS, "id", 1),
                Condition(ConditionEnum.EQUALS, "name", None, ChainEnum.OR),
            ),
            "returning": ("id",),
        },
    )


def test_update_execute(mock_database: Any, recording_dialect: Any) -> None:
    mock_database.query_with_params.return_value = result = Result()

    assert UpdateQuery(mock_database, recording_dialect, "users").set({"a": 1}).execute(True) is result
    mock_database.query_with_params.assert_called_once_with(QueryWithParams("UPDATE"), True)


def test_delete_defaults(mock_database: Any, recording_dialect: Any) -> None:
    DeleteQuery(mock_database, recording_dialect, "users").to_query_with_params()

    assert recording_dialect.last_call == ("delete", {"table": "users", "where": (), "returning": None})


def test_delete(mock_database: Any, recording_dialect: Any) -> None:
    delete = DeleteQuery(mock_database, recording_dialect, "users")

    assert delete.where_less_than("age", 18).returning() is delete
    assert delete.to_query_with_params() == QueryWithParams("DELETE")
    assert recording_dialect.last_call == (
        "delete",
        {"table": "users", "where": (Condition(ConditionEnum.LESS_THAN, "age", 18),), "returning": ()},
    )


def test_delete_execute(mock_database: Any, recording_dialect: Any) -> None:
    mock_database.query_with_params.return_value = result = Result()

    assert DeleteQuery(mock_database, recording_dialect, "users").execute() is result
    mock_database.query_with_params.assert_called_once_with(QueryWithParams("DELETE"), False)
