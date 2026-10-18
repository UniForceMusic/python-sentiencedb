"""Condition building by ``WhereMixin`` / ``HavingMixin`` and their groups."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from sentiencedb.dialects import SQLiteDialect
from sentiencedb.exceptions import QueryWithParamsError
from sentiencedb.queries import HavingGroup, SelectQuery, WhereGroup
from sentiencedb.queries.enums import ChainEnum, ConditionEnum
from sentiencedb.queries.objects.clauses import Condition, ConditionGroup, ConditionNode
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.queries.objects.sql import Expression, ExpressionF, Identifier, Raw

SUB_SELECT: Any = object()
"""Placeholder for a sub-select, swapped for a real ``SelectQuery`` by the ``sub_select`` fixture."""

AND, OR = ChainEnum.AND, ChainEnum.OR

# (method suffix, args, expected condition with AND chaining); "where" + suffix / "having" + suffix
CASES: list[tuple[str, tuple[Any, ...], ConditionNode]] = [
    ("_equals", ("a", 1), Condition(ConditionEnum.EQUALS, "a", 1)),
    ("_equals", (["t", "a"], "x", True), Condition(ConditionEnum.EQUALS, ["t", "a"], "x", cast=True)),
    ("_not_equals", ("a", None), Condition(ConditionEnum.NOT_EQUALS, "a", None)),
    ("_not_equals", ("a", 1.5, True), Condition(ConditionEnum.NOT_EQUALS, "a", 1.5, cast=True)),
    ("_is_null", ("a",), Condition(ConditionEnum.EQUALS, "a", None)),
    ("_is_not_null", ("a",), Condition(ConditionEnum.NOT_EQUALS, "a", None)),
    ("_like", ("a", "x%"), Condition(ConditionEnum.LIKE, "a", "x%")),
    ("_like", ("a", "x%", True), Condition(ConditionEnum.LIKE, "a", "x%", case_insensitive=True)),
    ("_not_like", ("a", "x%", True), Condition(ConditionEnum.NOT_LIKE, "a", "x%", case_insensitive=True)),
    ("_starts_with", ("a", "50%_"), Condition(ConditionEnum.LIKE, "a", "50\\%\\_%")),
    ("_ends_with", ("a", "x", True), Condition(ConditionEnum.LIKE, "a", "%x", case_insensitive=True)),
    ("_contains", ("a", "[a-c]^"), Condition(ConditionEnum.LIKE, "a", "%\\[a\\-c\\]\\^%")),
    ("_contains", ("a", "C:\\dir"), Condition(ConditionEnum.LIKE, "a", "%C:\\\\dir%")),
    ("_not_contains", ("a", "x"), Condition(ConditionEnum.NOT_LIKE, "a", "%x%")),
    ("_glob", ("a", "x*"), Condition(ConditionEnum.GLOB, "a", "x*")),
    ("_not_glob", ("a", "x?", True), Condition(ConditionEnum.NOT_GLOB, "a", "x?", case_insensitive=True)),
    ("_in", ("a", [1, 2]), Condition(ConditionEnum.IN, "a", (1, 2))),
    ("_in", ("a", SUB_SELECT), Condition(ConditionEnum.IN, "a", SUB_SELECT)),
    ("_not_in", ("a", []), Condition(ConditionEnum.NOT_IN, "a", ())),
    ("_less_than", ("a", 1), Condition(ConditionEnum.LESS_THAN, "a", 1)),
    ("_less_than_or_equals", ("a", 1), Condition(ConditionEnum.LESS_THAN_OR_EQUALS, "a", 1)),
    ("_greater_than", ("a", Raw("now()")), Condition(ConditionEnum.GREATER_THAN, "a", Raw("now()"))),
    ("_greater_than_or_equals", ("a", 1), Condition(ConditionEnum.GREATER_THAN_OR_EQUALS, "a", 1)),
    ("_between", ("a", 1, 9), Condition(ConditionEnum.BETWEEN, "a", (1, 9))),
    ("_not_between", ("a", 1, 9), Condition(ConditionEnum.NOT_BETWEEN, "a", (1, 9))),
    (
        "_empty",
        ("a",),
        ConditionGroup(
            (
                Condition(ConditionEnum.EQUALS, "a", None),
                Condition(ConditionEnum.EQUALS, "a", "0", OR),
                Condition(ConditionEnum.EQUALS, "a", "", OR, cast=True),
            )
        ),
    ),
    (
        "_not_empty",
        ("a",),
        ConditionGroup(
            (
                Condition(ConditionEnum.NOT_EQUALS, "a", None),
                Condition(ConditionEnum.NOT_EQUALS, "a", "0"),
                Condition(ConditionEnum.NOT_EQUALS, "a", "", cast=True),
            )
        ),
    ),
    ("_regex", ("a", "^x$"), Condition(ConditionEnum.REGEX, "a", "^x$")),
    ("_not_regex", ("a", "^x$", "i"), Condition(ConditionEnum.NOT_REGEX, "a", "^x$", flags="i")),
    ("_exists", (SUB_SELECT,), Condition(ConditionEnum.EXISTS, None, SUB_SELECT)),
    ("_not_exists", (SUB_SELECT,), Condition(ConditionEnum.NOT_EXISTS, None, SUB_SELECT)),
    ("_operator", ("a", "@>", [1, 2]), Condition("@>", "a", [1, 2])),
    ("_operator", ("a", ConditionEnum.GREATER_THAN, 1), Condition(">", "a", 1)),
    (
        "f",
        ("%s > %d", Identifier("a"), 1),
        Condition(ConditionEnum.RAW, None, ExpressionF("%s > %d", (Identifier("a"), 1))),
    ),
    ("", ("a = ? OR b = ?", [1, 2]), Condition(ConditionEnum.RAW, None, Expression("a = ? OR b = ?", (1, 2)))),
    ("", ("a = :a OR b = :a", {"a": 1}), Condition(ConditionEnum.RAW, None, Expression("a = :a OR b = :a", {"a": 1}))),
    ("", ("a IS NULL",), Condition(ConditionEnum.RAW, None, Expression("a IS NULL", ()))),
]


@pytest.fixture
def sub_select(mock_database: Any, recording_dialect: Any) -> SelectQuery:
    return SelectQuery(mock_database, recording_dialect, "t")


def _swap(value: Any, sub_select: SelectQuery) -> Any:
    if value is SUB_SELECT:
        return sub_select

    if isinstance(value, Condition) and value.value is SUB_SELECT:
        return replace(value, value=sub_select)

    return value


@pytest.mark.parametrize(("suffix", "args", "expected"), CASES)
@pytest.mark.parametrize(("group_class", "prefix"), [(WhereGroup, "where"), (HavingGroup, "having")])
@pytest.mark.parametrize("chain", [AND, OR])
def test_condition_methods(
    suffix: str,
    args: tuple[Any, ...],
    expected: ConditionNode,
    group_class: type[WhereGroup | HavingGroup],
    prefix: str,
    chain: ChainEnum,
    sub_select: SelectQuery,
) -> None:
    method = f"{prefix}{suffix}" if chain is AND else f"or_{prefix}{suffix}"
    group = group_class()

    returned = getattr(group, method)(*(_swap(arg, sub_select) for arg in args))

    assert returned is group
    assert group.conditions == (replace(_swap(expected, sub_select), chain=chain),)


@pytest.mark.parametrize("prefix", ["where", "having"])
def test_every_condition_method_has_an_or_twin(prefix: str) -> None:
    group = WhereGroup() if prefix == "where" else HavingGroup()
    methods = {name for name in dir(group) if name.startswith(prefix)}

    assert len(methods) == 31
    assert {f"or_{name}" for name in methods} <= set(dir(group))


def test_conditions_are_appended_in_order() -> None:
    group = WhereGroup().where_equals("a", 1).or_where_is_null("b").where_in("c", [1])

    assert [condition.chain for condition in group.conditions] == [AND, OR, AND]


def test_where_group_adds_a_group_with_chain_and_negation() -> None:
    group = (
        WhereGroup()
        .where_group(lambda inner: inner.where_equals("a", 1).or_where_equals("b", 2))
        .or_where_not_group(lambda inner: inner.where_equals("c", 3))
    )

    assert group.conditions == (
        ConditionGroup((Condition(ConditionEnum.EQUALS, "a", 1), Condition(ConditionEnum.EQUALS, "b", 2, OR)), AND),
        ConditionGroup((Condition(ConditionEnum.EQUALS, "c", 3),), OR, negate=True),
    )


def test_where_group_callback_may_return_none() -> None:
    def callback(inner: WhereGroup) -> None:
        inner.where_equals("a", 1)

    assert WhereGroup().where_not_group(callback).conditions == (
        ConditionGroup((Condition(ConditionEnum.EQUALS, "a", 1),), negate=True),
    )


def test_where_group_callback_may_return_another_group() -> None:
    group = WhereGroup().where_group(lambda _: WhereGroup().where_equals("x", 1))

    assert group.conditions == (ConditionGroup((Condition(ConditionEnum.EQUALS, "x", 1),)),)


def test_empty_groups_are_dropped() -> None:
    assert WhereGroup().where_group(lambda inner: inner).conditions == ()
    assert HavingGroup().or_having_not_group(lambda inner: None).conditions == ()


def test_groups_nest() -> None:
    group = WhereGroup().where_group(lambda outer: outer.where_group(lambda inner: inner.where_equals("a", 1)))

    assert group.conditions == (ConditionGroup((ConditionGroup((Condition(ConditionEnum.EQUALS, "a", 1),)),)),)


def test_having_group_callback_receives_a_having_group() -> None:
    received: list[object] = []

    HavingGroup().having_group(lambda inner: received.append(inner))

    assert isinstance(received[0], HavingGroup)


def test_raw_condition_named_params_are_made_positional_by_the_dialect(sqlite_dialect: SQLiteDialect) -> None:
    values = {"a": 1}
    (condition,) = WhereGroup().where("a = :a OR b = :a", values).conditions
    values["a"] = 2

    assert sqlite_dialect.select(table="t", where=[condition]) == QueryWithParams(
        'SELECT * FROM "t" WHERE (a = ? OR b = ?)', [1, 1]
    )

    with pytest.raises(QueryWithParamsError):
        sqlite_dialect.select(table="t", where=WhereGroup().where("a = :a", {"b": 1}).conditions)
