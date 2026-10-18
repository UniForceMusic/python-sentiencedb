"""``SelectQuery``: state handed to ``dialect.select`` and execution."""

from __future__ import annotations

from typing import Any

import pytest

from sentiencedb.queries import SelectQuery
from sentiencedb.queries.enums import ChainEnum, ConditionEnum, JoinEnum, OrderByDirectionEnum, UnionEnum
from sentiencedb.queries.objects.alias import Alias, SubQuery
from sentiencedb.queries.objects.clauses import Condition, JoinClause, OrderBy, UnionClause
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.queries.objects.sql import Raw
from sentiencedb.results.result import Result


@pytest.fixture
def select(mock_database: Any, recording_dialect: Any) -> SelectQuery:
    return SelectQuery(mock_database, recording_dialect, "users")


def _kwargs(select: SelectQuery, recording_dialect: Any) -> dict[str, Any]:
    assert select.to_query_with_params() == QueryWithParams("SELECT")
    method, kwargs = recording_dialect.last_call
    assert method == "select"

    return dict(kwargs)


def test_defaults(select: SelectQuery, recording_dialect: Any) -> None:
    assert _kwargs(select, recording_dialect) == {
        "table": "users",
        "columns": (),
        "distinct": None,
        "joins": (),
        "where": (),
        "group_by": (),
        "having": (),
        "order_by": (),
        "limit": None,
        "offset": None,
        "unions": (),
    }


def test_every_clause(select: SelectQuery, recording_dialect: Any, mock_database: Any) -> None:
    union = SelectQuery(mock_database, recording_dialect, "admins")
    union_all = SelectQuery(mock_database, recording_dialect, "guests")

    returned = (
        select.columns(["id", ["users", "name"]])
        .distinct(["id"])
        .left_join("orders")
        .where_equals("id", 1)
        .or_where_is_null("name")
        .group_by(["id"])
        .having_greater_than(Raw("count(*)"), 1)
        .order_by_asc("name")
        .order_by_desc(["users", "id"])
        .limit(10)
        .offset(5)
        .union(union)
        .union_all(union_all)
    )

    assert returned is select
    assert _kwargs(select, recording_dialect) == {
        "table": "users",
        "columns": ("id", ["users", "name"]),
        "distinct": ("id",),
        "joins": (JoinClause(JoinEnum.LEFT_JOIN, "orders"),),
        "where": (
            Condition(ConditionEnum.EQUALS, "id", 1),
            Condition(ConditionEnum.EQUALS, "name", None, ChainEnum.OR),
        ),
        "group_by": ("id",),
        "having": (Condition(ConditionEnum.GREATER_THAN, Raw("count(*)"), 1),),
        "order_by": (OrderBy("name", OrderByDirectionEnum.ASC), OrderBy(["users", "id"], OrderByDirectionEnum.DESC)),
        "limit": 10,
        "offset": 5,
        "unions": (UnionClause(UnionEnum.UNION, union), UnionClause(UnionEnum.UNION_ALL, union_all)),
    }


def test_table_may_be_an_alias_or_sub_query(mock_database: Any, recording_dialect: Any) -> None:
    inner = SelectQuery(mock_database, recording_dialect, "users")

    SelectQuery(mock_database, recording_dialect, Alias("users", "u")).to_query_with_params()
    assert recording_dialect.last_call[1]["table"] == Alias("users", "u")

    SelectQuery(mock_database, recording_dialect, SubQuery(inner, "u")).to_query_with_params()
    assert recording_dialect.last_call[1]["table"] == SubQuery(inner, "u")


def test_columns_mapping_aliases_by_key(select: SelectQuery, recording_dialect: Any, mock_database: Any) -> None:
    sub_select = SelectQuery(mock_database, recording_dialect, "orders")
    kept = Alias("email", "mail")

    select.columns({"n": "name", "q": ["users", "id"], "orders": sub_select, "ignored": kept})

    assert _kwargs(select, recording_dialect)["columns"] == (
        Alias("name", "n"),
        Alias(["users", "id"], "q"),
        SubQuery(sub_select, "orders"),
        kept,
    )


def test_columns_and_group_by_replace(select: SelectQuery, recording_dialect: Any) -> None:
    select.columns(["a"]).columns(["b"]).group_by(["a"]).group_by(["b"])

    kwargs = _kwargs(select, recording_dialect)

    assert (kwargs["columns"], kwargs["group_by"]) == (("b",), ("b",))


def test_plain_distinct(select: SelectQuery, recording_dialect: Any) -> None:
    assert _kwargs(select.distinct(), recording_dialect)["distinct"] == ()


@pytest.mark.parametrize(("limit", "expected"), [(10, 10), (0, 0), (-1, None)])
def test_limit(limit: int, expected: int | None, select: SelectQuery, recording_dialect: Any) -> None:
    assert _kwargs(select.limit(limit), recording_dialect)["limit"] == expected


@pytest.mark.parametrize(("offset", "expected"), [(5, 5), (0, None), (-1, None)])
def test_offset(offset: int, expected: int | None, select: SelectQuery, recording_dialect: Any) -> None:
    assert _kwargs(select.offset(offset), recording_dialect)["offset"] == expected


@pytest.mark.parametrize("emulate_prepare", [False, True])
def test_execute(emulate_prepare: bool, select: SelectQuery, mock_database: Any) -> None:
    result = Result({"id": None}, [{"id": 1}])
    mock_database.query_with_params.return_value = result

    assert select.execute(emulate_prepare) is result
    mock_database.query_with_params.assert_called_once_with(QueryWithParams("SELECT"), emulate_prepare)


def test_to_sql(select: SelectQuery) -> None:
    assert select.to_sql() == "SELECT"


@pytest.mark.parametrize(("rows", "expected"), [([{"count(*)": 3}], 3), ([{"count(*)": "7"}], 7), ([], 0)])
def test_count(
    rows: list[dict[str, Any]], expected: int, select: SelectQuery, recording_dialect: Any, mock_database: Any
) -> None:
    mock_database.query_with_params.return_value = Result({"count(*)": None}, rows)
    select.where_equals("active", True)

    assert select.count(emulate_prepare=True) == expected

    method, kwargs = recording_dialect.last_call
    assert method == "select"
    assert kwargs["table"] == SubQuery(select, "count")
    assert kwargs["columns"] == (Raw("count(*)"),)
    mock_database.query_with_params.assert_called_once_with(QueryWithParams("SELECT"), True)
