"""``HAVING`` conditions (PHP ``HavingTrait``)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Self

from ..enums import ChainEnum, ConditionEnum
from ..objects.clauses import Condition
from ..objects.sql import ExpressionF
from . import _conditions

if TYPE_CHECKING:
    from ..objects.clauses import ConditionNode
    from ..objects.condition_groups import HavingGroup
    from ..select_query import SelectQuery
    from ..type_aliases import IdentifierLike, Scalar, Value


class HavingMixin:
    """``HAVING`` conditions, chained with ``AND``; every method has an ``or_`` twin that chains with ``OR``.

    Mirrors :class:`WhereMixin`; also provides the body of a :class:`HavingGroup`.
    """

    _having: tuple[ConditionNode, ...] = ()

    def having_equals(self, column: IdentifierLike, value: Value, cast: bool = False) -> Self:
        """``column = value``; ``None`` gives ``IS NULL``. ``cast`` compares both sides cast to the value's type."""
        return self._add_having(Condition(ConditionEnum.EQUALS, column, value, cast=cast))

    def or_having_equals(self, column: IdentifierLike, value: Value, cast: bool = False) -> Self:
        """``OR`` twin of :meth:`having_equals`."""
        return self._add_having(Condition(ConditionEnum.EQUALS, column, value, ChainEnum.OR, cast=cast))

    def having_not_equals(self, column: IdentifierLike, value: Value, cast: bool = False) -> Self:
        """``column <> value``; ``None`` gives ``IS NOT NULL``. ``cast`` as in :meth:`having_equals`."""
        return self._add_having(Condition(ConditionEnum.NOT_EQUALS, column, value, cast=cast))

    def or_having_not_equals(self, column: IdentifierLike, value: Value, cast: bool = False) -> Self:
        """``OR`` twin of :meth:`having_not_equals`."""
        return self._add_having(Condition(ConditionEnum.NOT_EQUALS, column, value, ChainEnum.OR, cast=cast))

    def having_is_null(self, column: IdentifierLike) -> Self:
        """``column IS NULL``."""
        return self._add_having(Condition(ConditionEnum.EQUALS, column, None))

    def or_having_is_null(self, column: IdentifierLike) -> Self:
        """``OR`` twin of :meth:`having_is_null`."""
        return self._add_having(Condition(ConditionEnum.EQUALS, column, None, ChainEnum.OR))

    def having_is_not_null(self, column: IdentifierLike) -> Self:
        """``column IS NOT NULL``."""
        return self._add_having(Condition(ConditionEnum.NOT_EQUALS, column, None))

    def or_having_is_not_null(self, column: IdentifierLike) -> Self:
        """``OR`` twin of :meth:`having_is_not_null`."""
        return self._add_having(Condition(ConditionEnum.NOT_EQUALS, column, None, ChainEnum.OR))

    def having_like(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column LIKE value`` (``%`` and ``_`` wildcards, ``\\`` escapes)."""
        return self._add_having(Condition(ConditionEnum.LIKE, column, value, case_insensitive=case_insensitive))

    def or_having_like(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`having_like`."""
        return self._add_having(
            Condition(ConditionEnum.LIKE, column, value, ChainEnum.OR, case_insensitive=case_insensitive)
        )

    def having_not_like(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column NOT LIKE value``."""
        return self._add_having(Condition(ConditionEnum.NOT_LIKE, column, value, case_insensitive=case_insensitive))

    def or_having_not_like(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`having_not_like`."""
        return self._add_having(
            Condition(ConditionEnum.NOT_LIKE, column, value, ChainEnum.OR, case_insensitive=case_insensitive)
        )

    def having_starts_with(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column`` starts with ``value`` (matched literally)."""
        return self.having_like(column, f"{_conditions.escape_like(value)}%", case_insensitive)

    def or_having_starts_with(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`having_starts_with`."""
        return self.or_having_like(column, f"{_conditions.escape_like(value)}%", case_insensitive)

    def having_ends_with(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column`` ends with ``value`` (matched literally)."""
        return self.having_like(column, f"%{_conditions.escape_like(value)}", case_insensitive)

    def or_having_ends_with(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`having_ends_with`."""
        return self.or_having_like(column, f"%{_conditions.escape_like(value)}", case_insensitive)

    def having_contains(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column`` contains ``value`` (matched literally)."""
        return self.having_like(column, f"%{_conditions.escape_like(value)}%", case_insensitive)

    def or_having_contains(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`having_contains`."""
        return self.or_having_like(column, f"%{_conditions.escape_like(value)}%", case_insensitive)

    def having_not_contains(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column`` does not contain ``value`` (matched literally)."""
        return self.having_not_like(column, f"%{_conditions.escape_like(value)}%", case_insensitive)

    def or_having_not_contains(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`having_not_contains`."""
        return self.or_having_not_like(column, f"%{_conditions.escape_like(value)}%", case_insensitive)

    def having_glob(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column GLOB value`` (``*`` and ``?`` wildcards; converted to ``LIKE`` where unsupported)."""
        return self._add_having(Condition(ConditionEnum.GLOB, column, value, case_insensitive=case_insensitive))

    def or_having_glob(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`having_glob`."""
        return self._add_having(
            Condition(ConditionEnum.GLOB, column, value, ChainEnum.OR, case_insensitive=case_insensitive)
        )

    def having_not_glob(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column NOT GLOB value``."""
        return self._add_having(Condition(ConditionEnum.NOT_GLOB, column, value, case_insensitive=case_insensitive))

    def or_having_not_glob(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`having_not_glob`."""
        return self._add_having(
            Condition(ConditionEnum.NOT_GLOB, column, value, ChainEnum.OR, case_insensitive=case_insensitive)
        )

    def having_in(self, column: IdentifierLike, values: Sequence[Value] | SelectQuery) -> Self:
        """``column IN (values)`` or ``column IN (<select>)``; an empty list never matches."""
        return self._add_having(Condition(ConditionEnum.IN, column, values))

    def or_having_in(self, column: IdentifierLike, values: Sequence[Value] | SelectQuery) -> Self:
        """``OR`` twin of :meth:`having_in`."""
        return self._add_having(Condition(ConditionEnum.IN, column, values, ChainEnum.OR))

    def having_not_in(self, column: IdentifierLike, values: Sequence[Value] | SelectQuery) -> Self:
        """``column NOT IN (values)``; an empty list always matches."""
        return self._add_having(Condition(ConditionEnum.NOT_IN, column, values))

    def or_having_not_in(self, column: IdentifierLike, values: Sequence[Value] | SelectQuery) -> Self:
        """``OR`` twin of :meth:`having_not_in`."""
        return self._add_having(Condition(ConditionEnum.NOT_IN, column, values, ChainEnum.OR))

    def having_less_than(self, column: IdentifierLike, value: Value) -> Self:
        """``column < value``."""
        return self._add_having(Condition(ConditionEnum.LESS_THAN, column, value))

    def or_having_less_than(self, column: IdentifierLike, value: Value) -> Self:
        """``OR`` twin of :meth:`having_less_than`."""
        return self._add_having(Condition(ConditionEnum.LESS_THAN, column, value, ChainEnum.OR))

    def having_less_than_or_equals(self, column: IdentifierLike, value: Value) -> Self:
        """``column <= value``."""
        return self._add_having(Condition(ConditionEnum.LESS_THAN_OR_EQUALS, column, value))

    def or_having_less_than_or_equals(self, column: IdentifierLike, value: Value) -> Self:
        """``OR`` twin of :meth:`having_less_than_or_equals`."""
        return self._add_having(Condition(ConditionEnum.LESS_THAN_OR_EQUALS, column, value, ChainEnum.OR))

    def having_greater_than(self, column: IdentifierLike, value: Value) -> Self:
        """``column > value``."""
        return self._add_having(Condition(ConditionEnum.GREATER_THAN, column, value))

    def or_having_greater_than(self, column: IdentifierLike, value: Value) -> Self:
        """``OR`` twin of :meth:`having_greater_than`."""
        return self._add_having(Condition(ConditionEnum.GREATER_THAN, column, value, ChainEnum.OR))

    def having_greater_than_or_equals(self, column: IdentifierLike, value: Value) -> Self:
        """``column >= value``."""
        return self._add_having(Condition(ConditionEnum.GREATER_THAN_OR_EQUALS, column, value))

    def or_having_greater_than_or_equals(self, column: IdentifierLike, value: Value) -> Self:
        """``OR`` twin of :meth:`having_greater_than_or_equals`."""
        return self._add_having(Condition(ConditionEnum.GREATER_THAN_OR_EQUALS, column, value, ChainEnum.OR))

    def having_between(self, column: IdentifierLike, min: Value, max: Value) -> Self:
        """``column BETWEEN min AND max``."""
        return self._add_having(Condition(ConditionEnum.BETWEEN, column, (min, max)))

    def or_having_between(self, column: IdentifierLike, min: Value, max: Value) -> Self:
        """``OR`` twin of :meth:`having_between`."""
        return self._add_having(Condition(ConditionEnum.BETWEEN, column, (min, max), ChainEnum.OR))

    def having_not_between(self, column: IdentifierLike, min: Value, max: Value) -> Self:
        """``column NOT BETWEEN min AND max``."""
        return self._add_having(Condition(ConditionEnum.NOT_BETWEEN, column, (min, max)))

    def or_having_not_between(self, column: IdentifierLike, min: Value, max: Value) -> Self:
        """``OR`` twin of :meth:`having_not_between`."""
        return self._add_having(Condition(ConditionEnum.NOT_BETWEEN, column, (min, max), ChainEnum.OR))

    def having_empty(self, column: IdentifierLike) -> Self:
        """``column`` is ``NULL``, ``0`` or ``''``."""
        return self._add_having(_conditions.empty(column, ChainEnum.AND))

    def or_having_empty(self, column: IdentifierLike) -> Self:
        """``OR`` twin of :meth:`having_empty`."""
        return self._add_having(_conditions.empty(column, ChainEnum.OR))

    def having_not_empty(self, column: IdentifierLike) -> Self:
        """``column`` is not ``NULL``, ``0`` or ``''``."""
        return self._add_having(_conditions.not_empty(column, ChainEnum.AND))

    def or_having_not_empty(self, column: IdentifierLike) -> Self:
        """``OR`` twin of :meth:`having_not_empty`."""
        return self._add_having(_conditions.not_empty(column, ChainEnum.OR))

    def having_regex(self, column: IdentifierLike, pattern: str, flags: str = "") -> Self:
        """``column`` matches the regular expression ``pattern``; ``flags`` such as ``"i"``."""
        return self._add_having(Condition(ConditionEnum.REGEX, column, pattern, flags=flags))

    def or_having_regex(self, column: IdentifierLike, pattern: str, flags: str = "") -> Self:
        """``OR`` twin of :meth:`having_regex`."""
        return self._add_having(Condition(ConditionEnum.REGEX, column, pattern, ChainEnum.OR, flags=flags))

    def having_not_regex(self, column: IdentifierLike, pattern: str, flags: str = "") -> Self:
        """``column`` does not match the regular expression ``pattern``."""
        return self._add_having(Condition(ConditionEnum.NOT_REGEX, column, pattern, flags=flags))

    def or_having_not_regex(self, column: IdentifierLike, pattern: str, flags: str = "") -> Self:
        """``OR`` twin of :meth:`having_not_regex`."""
        return self._add_having(Condition(ConditionEnum.NOT_REGEX, column, pattern, ChainEnum.OR, flags=flags))

    def having_exists(self, select_query: SelectQuery) -> Self:
        """``EXISTS (<select>)``."""
        return self._add_having(Condition(ConditionEnum.EXISTS, None, select_query))

    def or_having_exists(self, select_query: SelectQuery) -> Self:
        """``OR`` twin of :meth:`having_exists`."""
        return self._add_having(Condition(ConditionEnum.EXISTS, None, select_query, ChainEnum.OR))

    def having_not_exists(self, select_query: SelectQuery) -> Self:
        """``NOT EXISTS (<select>)``."""
        return self._add_having(Condition(ConditionEnum.NOT_EXISTS, None, select_query))

    def or_having_not_exists(self, select_query: SelectQuery) -> Self:
        """``OR`` twin of :meth:`having_not_exists`."""
        return self._add_having(Condition(ConditionEnum.NOT_EXISTS, None, select_query, ChainEnum.OR))

    def having_group(self, callback: Callable[[HavingGroup], HavingGroup | None]) -> Self:
        """``(<conditions>)``: ``callback`` fills a fresh :class:`HavingGroup`. An empty group is left out."""
        return self._add_having_group(callback, ChainEnum.AND, negate=False)

    def or_having_group(self, callback: Callable[[HavingGroup], HavingGroup | None]) -> Self:
        """``OR`` twin of :meth:`having_group`."""
        return self._add_having_group(callback, ChainEnum.OR, negate=False)

    def having_not_group(self, callback: Callable[[HavingGroup], HavingGroup | None]) -> Self:
        """``NOT (<conditions>)``; see :meth:`having_group`."""
        return self._add_having_group(callback, ChainEnum.AND, negate=True)

    def or_having_not_group(self, callback: Callable[[HavingGroup], HavingGroup | None]) -> Self:
        """``OR`` twin of :meth:`having_not_group`."""
        return self._add_having_group(callback, ChainEnum.OR, negate=True)

    def having_operator(
        self, column: IdentifierLike, operator: str | ConditionEnum, value: Value | Sequence[Value]
    ) -> Self:
        """``column <operator> value`` with the operator emitted verbatim; a sequence renders ``(?, ?, ..)``."""
        return self._add_having(Condition(str(operator), column, value))

    def or_having_operator(
        self, column: IdentifierLike, operator: str | ConditionEnum, value: Value | Sequence[Value]
    ) -> Self:
        """``OR`` twin of :meth:`having_operator`."""
        return self._add_having(Condition(str(operator), column, value, ChainEnum.OR))

    def havingf(self, format: str, *values: Value) -> Self:
        """A printf style condition, e.g. ``havingf("%s > %d", identifier("age"), 18)``; see :class:`ExpressionF`."""
        return self._add_having(Condition(ConditionEnum.RAW, None, ExpressionF(format, values)))

    def or_havingf(self, format: str, *values: Value) -> Self:
        """``OR`` twin of :meth:`havingf`."""
        return self._add_having(Condition(ConditionEnum.RAW, None, ExpressionF(format, values), ChainEnum.OR))

    def having(self, sql: str, values: Sequence[Scalar] | Mapping[str, Scalar] = ()) -> Self:
        """A raw ``(<sql>)`` condition with ``?`` placeholders and positional values, or ``:name`` placeholders
        and a mapping."""
        return self._add_having(_conditions.raw(sql, values, ChainEnum.AND))

    def or_having(self, sql: str, values: Sequence[Scalar] | Mapping[str, Scalar] = ()) -> Self:
        """``OR`` twin of :meth:`having`."""
        return self._add_having(_conditions.raw(sql, values, ChainEnum.OR))

    def _add_having(self, condition: ConditionNode) -> Self:
        self._having += (condition,)

        return self

    def _add_having_group(
        self, callback: Callable[[HavingGroup], HavingGroup | None], chain: ChainEnum, negate: bool
    ) -> Self:
        from ..objects.condition_groups import HavingGroup  # circular: HavingGroup is built on this mixin

        group = _conditions.group(HavingGroup(), callback, chain, negate)

        return self if group is None else self._add_having(group)


__all__ = ["HavingMixin"]
