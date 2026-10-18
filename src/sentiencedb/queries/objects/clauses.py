"""Plain data describing statement clauses, produced by builders and consumed by dialects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from ..enums import ChainEnum, ConditionEnum, JoinEnum, OrderByDirectionEnum, UnionEnum

if TYPE_CHECKING:
    from ..query import Query
    from ..type_aliases import ConditionValue, IdentifierLike, TableRef, Value


@dataclass(frozen=True, slots=True)
class Condition:
    """One condition of a ``WHERE``, ``HAVING`` or join ``ON`` clause.

    ``condition`` is a :class:`ConditionEnum`, or a plain ``str`` operator that is emitted verbatim
    (``where_operator``). The shape of ``identifier`` and ``value`` depends on the kind:

    =============================================  ==========  ============================================
    kind                                           identifier  value
    =============================================  ==========  ============================================
    ``EQUALS`` / ``NOT_EQUALS``                    column      ``Value``; ``None`` renders ``IS [NOT] NULL``;
                                                               ``cast`` wraps both sides in ``cast(.. AS T)``
    ``LESS_THAN`` ... ``GREATER_THAN_OR_EQUALS``   column      ``Value``
    ``BETWEEN`` / ``NOT_BETWEEN``                  column      ``(min, max)`` tuple of ``Value``
    ``LIKE`` / ``NOT_LIKE``                        column      ``str`` pattern; ``case_insensitive``
    ``GLOB`` / ``NOT_GLOB``                        column      ``str`` pattern; ``case_insensitive``
    ``IN`` / ``NOT_IN``                            column      ``Sequence[Value]`` or a ``Query``
    ``REGEX`` / ``NOT_REGEX``                      column      ``str`` pattern; ``flags``
    ``EXISTS`` / ``NOT_EXISTS``                    ``None``    ``Query``
    ``RAW``                                        ``None``    ``Sql`` (rendered as ``(<sql>)``)
    ``str`` operator                               column      ``Value`` or ``Sequence[Value]``
    =============================================  ==========  ============================================
    """

    condition: ConditionEnum | str
    identifier: IdentifierLike | None
    value: ConditionValue
    chain: ChainEnum = ChainEnum.AND
    case_insensitive: bool = False
    cast: bool = False
    flags: str = ""


@dataclass(frozen=True, slots=True)
class ConditionGroup:
    """A parenthesised group of conditions: ``[NOT ](<conditions>)``. Never empty."""

    conditions: Sequence[ConditionNode]
    chain: ChainEnum = ChainEnum.AND
    negate: bool = False


ConditionNode: TypeAlias = Condition | ConditionGroup


@dataclass(frozen=True, slots=True)
class JoinClause:
    """``<join> <table> [ON <conditions>]``.

    Without conditions ``LEFT``/``INNER`` joins render ``ON TRUE`` (as the dialect spells true);
    ``CROSS`` joins render no ``ON`` clause at all.
    """

    join: JoinEnum
    table: TableRef
    conditions: Sequence[ConditionNode] = ()


@dataclass(frozen=True, slots=True)
class OnConflict:
    """Upsert behaviour of an ``INSERT``.

    :param conflict: A constraint name (``str``) or the conflicting columns.
    :param updates: ``None`` means ``DO NOTHING``; an empty mapping updates every inserted column with its new
        value; otherwise the explicit ``column -> value`` updates.
    """

    conflict: str | Sequence[str]
    updates: Mapping[str, Value] | None = None


@dataclass(frozen=True, slots=True)
class OrderBy:
    """``<column> ASC|DESC``."""

    column: IdentifierLike
    direction: OrderByDirectionEnum = OrderByDirectionEnum.ASC


@dataclass(frozen=True, slots=True)
class UnionClause:
    """``UNION [ALL] (<select>)``."""

    union: UnionEnum
    select_query: Query


__all__ = ["Condition", "ConditionGroup", "ConditionNode", "JoinClause", "OnConflict", "OrderBy", "UnionClause"]
