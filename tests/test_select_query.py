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
    select.columns(
        {
            "n": "name",
            "q": ["users", "id"],
            "orders": sub_select,
            "mail": Alias("email", "ignored"),
            "latest": SubQuery(sub_select, "ignored"),
        }
    )

    assert _kwargs(select, recording_dialect)["columns"] == (
        Alias("name", "n"),
        Alias(["users", "id"], "q"),
        SubQuery(sub_select, "orders"),
        Alias("email", "mail"),
        SubQuery(sub_select, "latest"),
    )


def test_a_str_is_one_column(select: SelectQuery, recording_dialect: Any) -> None:
    select.columns("email").group_by("email").distinct("email")

    kwargs = _kwargs(select, recording_dialect)

    assert (kwargs["columns"], kwargs["group_by"], kwargs["distinct"]) == (("email",), ("email",), ("email",))


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
    assert kwargs["table"].alias == "count"
    assert kwargs["columns"] == (Raw("count(*)"),)
    mock_database.query_with_params.assert_called_once_with(QueryWithParams("SELECT"), True)


def _counted(select: SelectQuery, recording_dialect: Any, mock_database: Any) -> dict[str, Any]:
    """The keyword arguments the inner select of ``select.count()`` hands to the dialect."""
    mock_database.query_with_params.return_value = Result({"count(*)": None}, [{"count(*)": 1}])
    select.count()
    inner = recording_dialect.last_call[1]["table"].select_query
    inner.to_query_with_params()

    return dict(recording_dialect.last_call[1])


@pytest.mark.parametrize(
    "build",
    [
        lambda s: s,
        lambda s: s.columns([["users", "id"], ["posts", "id"]]),
        lambda s: s.columns({"n": "name"}),
        lambda s: s.columns([Raw("count(*)")]).group_by(["name"]),
        lambda s: s.order_by_asc("name"),
    ],
)
def test_count_selects_one_where_the_select_list_cannot_change_the_rows(
    build: Any, select: SelectQuery, recording_dialect: Any, mock_database: Any
) -> None:
    """MySQL rejects a derived table with duplicate column names, which a joined ``SELECT *`` produces."""
    kwargs = _counted(build(select), recording_dialect, mock_database)

    assert kwargs["columns"] == (Raw("1"),)
    assert kwargs["order_by"] == ()


@pytest.mark.parametrize(
    "build",
    [
        lambda s: s.columns(["name"]).distinct(),
        lambda s: s.columns(["name"]).union(s.__class__(s._database, s._dialect, "other")),
        lambda s: s.columns(["name"]).group_by(["name"]).having_equals("name", "a"),
        lambda s: s.columns([Raw("max(id)")]),
        lambda s: s.columns({"n": "name"}).order_by_asc("n").limit(2),
    ],
)
def test_count_keeps_a_select_list_that_decides_the_rows(
    build: Any, select: SelectQuery, recording_dialect: Any, mock_database: Any
) -> None:
    query = build(select)
    columns = query._columns

    assert _counted(query, recording_dialect, mock_database)["columns"] == columns


def test_count_keeps_order_by_with_a_limit(select: SelectQuery, recording_dialect: Any, mock_database: Any) -> None:
    kwargs = _counted(select.order_by_desc("id").limit(3), recording_dialect, mock_database)

    assert (kwargs["columns"], kwargs["limit"]) == ((), 3)
    assert len(kwargs["order_by"]) == 1


def test_count_leaves_the_query_unchanged(select: SelectQuery, recording_dialect: Any, mock_database: Any) -> None:
    select.columns(["name"]).order_by_asc("name")
    _counted(select, recording_dialect, mock_database)

    kwargs = _kwargs(select, recording_dialect)

    assert (kwargs["columns"], len(kwargs["order_by"])) == (("name",), 1)
