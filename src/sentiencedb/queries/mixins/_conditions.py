"""Condition building shared by :class:`WhereMixin` and :class:`HavingMixin` (PHP ``ConditionsTrait``)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, TypeVar

from ..enums import ChainEnum, ConditionEnum
from ..helpers import escape_backslash
from ..objects.clauses import Condition, ConditionGroup
from ..objects.query_with_params import QueryWithParams
from ..objects.sql import Expression

if TYPE_CHECKING:
    from ..objects.condition_groups import HavingGroup, WhereGroup
    from ..type_aliases import IdentifierLike, Scalar

_GroupT = TypeVar("_GroupT", "WhereGroup", "HavingGroup")

_LIKE_CHARS = ("%", "_", "-", "^", "[", "]")


def escape_like(value: str) -> str:
    """Escape ``value`` so a ``LIKE`` pattern matches it literally."""
    return escape_backslash(value, _LIKE_CHARS)


def empty(column: IdentifierLike, chain: ChainEnum) -> ConditionGroup:
    """``(column IS NULL OR column = '0' OR cast(column) = cast(''))``.

    Zero is compared as the string ``'0'``, which every engine coerces to the column's type: an integer ``0`` makes
    PostgreSQL reject text and boolean columns, and makes MySQL match every non-numeric string.
    """
    return ConditionGroup(
        (
            Condition(ConditionEnum.EQUALS, column, None),
            Condition(ConditionEnum.EQUALS, column, "0", ChainEnum.OR),
            Condition(ConditionEnum.EQUALS, column, "", ChainEnum.OR, cast=True),
        ),
        chain,
    )


def not_empty(column: IdentifierLike, chain: ChainEnum) -> ConditionGroup:
    """``(column IS NOT NULL AND column <> '0' AND cast(column) <> cast(''))``; see :func:`empty`."""
    return ConditionGroup(
        (
            Condition(ConditionEnum.NOT_EQUALS, column, None),
            Condition(ConditionEnum.NOT_EQUALS, column, "0"),
            Condition(ConditionEnum.NOT_EQUALS, column, "", cast=True),
        ),
        chain,
    )


def raw(sql: str, values: Sequence[Scalar] | Mapping[str, Scalar], chain: ChainEnum) -> Condition:
    """A raw ``(<sql>)`` condition; ``:name`` placeholders are rewritten to ``?``."""
    query_with_params = QueryWithParams(sql, values).named_params_to_question_marks()

    return Condition(
        ConditionEnum.RAW, None, Expression(query_with_params.query, list(query_with_params.params)), chain
    )


def group(
    group: _GroupT, callback: Callable[[_GroupT], _GroupT | None], chain: ChainEnum, negate: bool
) -> ConditionGroup | None:
    """Let ``callback`` fill ``group`` (or return a replacement); ``None`` when it ends up empty."""
    conditions = (callback(group) or group).conditions

    return ConditionGroup(conditions, chain, negate) if conditions else None
