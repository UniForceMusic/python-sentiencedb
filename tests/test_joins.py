"""``JoinsMixin`` and the ``Join`` ON builder, checked through the select handoff."""

from __future__ import annotations

from typing import Any

import pytest

from sentiencedb.queries import Join, SelectQuery
from sentiencedb.queries.enums import ChainEnum, ConditionEnum, JoinEnum
from sentiencedb.queries.objects.alias import Alias, SubQuery
from sentiencedb.queries.objects.clauses import Condition, JoinClause
from sentiencedb.queries.objects.sql import ExpressionF, Identifier, Raw


@pytest.fixture
def select(mock_database: Any, recording_dialect: Any) -> SelectQuery:
    return SelectQuery(mock_database, recording_dialect, "users")


def _joins(select: SelectQuery, recording_dialect: Any) -> Any:
    select.to_query_with_params()

    return recording_dialect.last_call[1]["joins"]


def _on(join: Join) -> Join:
    return join.on(["orders", "user_id"], ["users", "id"])


ON = (Condition(ConditionEnum.EQUALS, ["orders", "user_id"], Identifier(["users", "id"])),)


@pytest.mark.parametrize(
    ("method", "kind"),
    [
        ("left_join", JoinEnum.LEFT_JOIN),
        ("inner_join", JoinEnum.INNER_JOIN),
        ("left_join_lateral", JoinEnum.LEFT_JOIN_LATERAL),
        ("inner_join_lateral", JoinEnum.INNER_JOIN_LATERAL),
        ("cross_join_lateral", JoinEnum.CROSS_JOIN_LATERAL),
        ("outer_apply", JoinEnum.LEFT_JOIN_LATERAL),
        ("cross_apply", JoinEnum.INNER_JOIN_LATERAL),
    ],
)
def test_join_kinds_in_three_forms(
    method: str, kind: JoinEnum, select: SelectQuery, recording_dialect: Any, mock_database: Any
) -> None:
    sub_select = SelectQuery(mock_database, recording_dialect, "orders")

    assert getattr(select, method)("orders", _on) is select
    getattr(select, f"{method}_table")(["shop", "orders"], _on, "o")
    getattr(select, f"{method}_table")("orders")
    getattr(select, f"{method}_sub_query")(sub_select, "o", _on)

    assert _joins(select, recording_dialect) == (
        JoinClause(kind, "orders", ON),
        JoinClause(kind, Alias(["shop", "orders"], "o"), ON),
        JoinClause(kind, "orders"),
        JoinClause(kind, SubQuery(sub_select, "o"), ON),
    )


def test_cross_joins_take_no_conditions(select: SelectQuery, recording_dialect: Any, mock_database: Any) -> None:
    sub_select = SelectQuery(mock_database, recording_dialect, "sizes")

    select.cross_join("colors").cross_join_table("sizes", "s").cross_join_sub_query(sub_select, "x")

    assert _joins(select, recording_dialect) == (
        JoinClause(JoinEnum.CROSS_JOIN, "colors"),
        JoinClause(JoinEnum.CROSS_JOIN, Alias("sizes", "s")),
        JoinClause(JoinEnum.CROSS_JOIN, SubQuery(sub_select, "x")),
    )


def test_on_conditions_use_every_where_method(select: SelectQuery, recording_dialect: Any) -> None:
    select.left_join(
        "orders",
        lambda join: (
            join.on(["orders", "user_id"], ["users", "id"])
            .or_on(["orders", "owner_id"], ["users", "id"])
            .where_greater_than(["orders", "total"], 10)
        ),
    )

    assert _joins(select, recording_dialect) == (
        JoinClause(
            JoinEnum.LEFT_JOIN,
            "orders",
            (
                *ON,
                Condition(ConditionEnum.EQUALS, ["orders", "owner_id"], Identifier(["users", "id"]), ChainEnum.OR),
                Condition(ConditionEnum.GREATER_THAN, ["orders", "total"], 10),
            ),
        ),
    )


def test_on_callback_may_return_none(select: SelectQuery, recording_dialect: Any) -> None:
    def on(join: Join) -> None:
        join.on(["orders", "user_id"], ["users", "id"])

    select.inner_join("orders", on)

    assert _joins(select, recording_dialect) == (JoinClause(JoinEnum.INNER_JOIN, "orders", ON),)


def test_raw_joins_keep_their_position(select: SelectQuery, recording_dialect: Any) -> None:
    select.join("NATURAL JOIN a").left_join("b").joinf("JOIN c ON c.x = %d", 1)

    assert _joins(select, recording_dialect) == (
        Raw("NATURAL JOIN a"),
        JoinClause(JoinEnum.LEFT_JOIN, "b"),
        ExpressionF("JOIN c ON c.x = %d", (1,)),
    )


def test_join_conditions_are_read_when_compiling(select: SelectQuery, recording_dialect: Any) -> None:
    joins: list[Join] = []
    select.left_join("orders", lambda join: joins.append(join))

    joins[0].where_equals("a", 1)

    assert _joins(select, recording_dialect) == (
        JoinClause(JoinEnum.LEFT_JOIN, "orders", (Condition(ConditionEnum.EQUALS, "a", 1),)),
    )
