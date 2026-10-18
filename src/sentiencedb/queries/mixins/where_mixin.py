"""``WHERE`` conditions (PHP ``WhereTrait``)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Self

from ..enums import ChainEnum, ConditionEnum
from ..objects.clauses import Condition
from ..objects.sql import ExpressionF
from . import _conditions

if TYPE_CHECKING:
    from ..objects.clauses import ConditionNode
    from ..objects.condition_groups import WhereGroup
    from ..select_query import SelectQuery
    from ..type_aliases import IdentifierLike, Scalar, Value


class WhereMixin:
    """``WHERE`` conditions, chained with ``AND``; every method has an ``or_`` twin that chains with ``OR``.

    Also provides the ``ON`` conditions of a :class:`Join` and the body of a :class:`WhereGroup`.
    """

    _where: tuple[ConditionNode, ...] = ()

    def where_equals(self, column: IdentifierLike, value: Value, cast: bool = False) -> Self:
        """``column = value``; ``None`` gives ``IS NULL``. ``cast`` compares both sides cast to the value's type."""
        return self._add_where(Condition(ConditionEnum.EQUALS, column, value, cast=cast))

    def or_where_equals(self, column: IdentifierLike, value: Value, cast: bool = False) -> Self:
        """``OR`` twin of :meth:`where_equals`."""
        return self._add_where(Condition(ConditionEnum.EQUALS, column, value, ChainEnum.OR, cast=cast))

    def where_not_equals(self, column: IdentifierLike, value: Value, cast: bool = False) -> Self:
        """``column <> value``; ``None`` gives ``IS NOT NULL``. ``cast`` as in :meth:`where_equals`."""
        return self._add_where(Condition(ConditionEnum.NOT_EQUALS, column, value, cast=cast))

    def or_where_not_equals(self, column: IdentifierLike, value: Value, cast: bool = False) -> Self:
        """``OR`` twin of :meth:`where_not_equals`."""
        return self._add_where(Condition(ConditionEnum.NOT_EQUALS, column, value, ChainEnum.OR, cast=cast))

    def where_is_null(self, column: IdentifierLike) -> Self:
        """``column IS NULL``."""
        return self._add_where(Condition(ConditionEnum.EQUALS, column, None))

    def or_where_is_null(self, column: IdentifierLike) -> Self:
        """``OR`` twin of :meth:`where_is_null`."""
        return self._add_where(Condition(ConditionEnum.EQUALS, column, None, ChainEnum.OR))

    def where_is_not_null(self, column: IdentifierLike) -> Self:
        """``column IS NOT NULL``."""
        return self._add_where(Condition(ConditionEnum.NOT_EQUALS, column, None))

    def or_where_is_not_null(self, column: IdentifierLike) -> Self:
        """``OR`` twin of :meth:`where_is_not_null`."""
        return self._add_where(Condition(ConditionEnum.NOT_EQUALS, column, None, ChainEnum.OR))

    def where_like(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column LIKE value`` (``%`` and ``_`` wildcards, ``\\`` escapes)."""
        return self._add_where(Condition(ConditionEnum.LIKE, column, value, case_insensitive=case_insensitive))

    def or_where_like(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`where_like`."""
        return self._add_where(
            Condition(ConditionEnum.LIKE, column, value, ChainEnum.OR, case_insensitive=case_insensitive)
        )

    def where_not_like(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column NOT LIKE value``."""
        return self._add_where(Condition(ConditionEnum.NOT_LIKE, column, value, case_insensitive=case_insensitive))

    def or_where_not_like(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`where_not_like`."""
        return self._add_where(
            Condition(ConditionEnum.NOT_LIKE, column, value, ChainEnum.OR, case_insensitive=case_insensitive)
        )

    def where_starts_with(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column`` starts with ``value`` (matched literally)."""
        return self.where_like(column, f"{_conditions.escape_like(value)}%", case_insensitive)

    def or_where_starts_with(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`where_starts_with`."""
        return self.or_where_like(column, f"{_conditions.escape_like(value)}%", case_insensitive)

    def where_ends_with(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column`` ends with ``value`` (matched literally)."""
        return self.where_like(column, f"%{_conditions.escape_like(value)}", case_insensitive)

    def or_where_ends_with(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`where_ends_with`."""
        return self.or_where_like(column, f"%{_conditions.escape_like(value)}", case_insensitive)

    def where_contains(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column`` contains ``value`` (matched literally)."""
        return self.where_like(column, f"%{_conditions.escape_like(value)}%", case_insensitive)

    def or_where_contains(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`where_contains`."""
        return self.or_where_like(column, f"%{_conditions.escape_like(value)}%", case_insensitive)

    def where_not_contains(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column`` does not contain ``value`` (matched literally)."""
        return self.where_not_like(column, f"%{_conditions.escape_like(value)}%", case_insensitive)

    def or_where_not_contains(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`where_not_contains`."""
        return self.or_where_not_like(column, f"%{_conditions.escape_like(value)}%", case_insensitive)

    def where_glob(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column GLOB value`` (``*`` and ``?`` wildcards; converted to ``LIKE`` where unsupported)."""
        return self._add_where(Condition(ConditionEnum.GLOB, column, value, case_insensitive=case_insensitive))

    def or_where_glob(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`where_glob`."""
        return self._add_where(
            Condition(ConditionEnum.GLOB, column, value, ChainEnum.OR, case_insensitive=case_insensitive)
        )

    def where_not_glob(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``column NOT GLOB value``."""
        return self._add_where(Condition(ConditionEnum.NOT_GLOB, column, value, case_insensitive=case_insensitive))

    def or_where_not_glob(self, column: IdentifierLike, value: str, case_insensitive: bool = False) -> Self:
        """``OR`` twin of :meth:`where_not_glob`."""
        return self._add_where(
            Condition(ConditionEnum.NOT_GLOB, column, value, ChainEnum.OR, case_insensitive=case_insensitive)
        )

    def where_in(self, column: IdentifierLike, values: Sequence[Value] | SelectQuery) -> Self:
        """``column IN (values)`` or ``column IN (<select>)``; an empty list never matches."""
        return self._add_where(Condition(ConditionEnum.IN, column, _conditions.in_values(values)))

    def or_where_in(self, column: IdentifierLike, values: Sequence[Value] | SelectQuery) -> Self:
        """``OR`` twin of :meth:`where_in`."""
        return self._add_where(Condition(ConditionEnum.IN, column, _conditions.in_values(values), ChainEnum.OR))

    def where_not_in(self, column: IdentifierLike, values: Sequence[Value] | SelectQuery) -> Self:
        """``column NOT IN (values)``; an empty list always matches."""
        return self._add_where(Condition(ConditionEnum.NOT_IN, column, _conditions.in_values(values)))

    def or_where_not_in(self, column: IdentifierLike, values: Sequence[Value] | SelectQuery) -> Self:
        """``OR`` twin of :meth:`where_not_in`."""
        return self._add_where(Condition(ConditionEnum.NOT_IN, column, _conditions.in_values(values), ChainEnum.OR))

    def where_less_than(self, column: IdentifierLike, value: Value) -> Self:
        """``column < value``."""
        return self._add_where(Condition(ConditionEnum.LESS_THAN, column, value))

    def or_where_less_than(self, column: IdentifierLike, value: Value) -> Self:
        """``OR`` twin of :meth:`where_less_than`."""
        return self._add_where(Condition(ConditionEnum.LESS_THAN, column, value, ChainEnum.OR))

    def where_less_than_or_equals(self, column: IdentifierLike, value: Value) -> Self:
        """``column <= value``."""
        return self._add_where(Condition(ConditionEnum.LESS_THAN_OR_EQUALS, column, value))

    def or_where_less_than_or_equals(self, column: IdentifierLike, value: Value) -> Self:
        """``OR`` twin of :meth:`where_less_than_or_equals`."""
        return self._add_where(Condition(ConditionEnum.LESS_THAN_OR_EQUALS, column, value, ChainEnum.OR))

    def where_greater_than(self, column: IdentifierLike, value: Value) -> Self:
        """``column > value``."""
        return self._add_where(Condition(ConditionEnum.GREATER_THAN, column, value))

    def or_where_greater_than(self, column: IdentifierLike, value: Value) -> Self:
        """``OR`` twin of :meth:`where_greater_than`."""
        return self._add_where(Condition(ConditionEnum.GREATER_THAN, column, value, ChainEnum.OR))

    def where_greater_than_or_equals(self, column: IdentifierLike, value: Value) -> Self:
        """``column >= value``."""
        return self._add_where(Condition(ConditionEnum.GREATER_THAN_OR_EQUALS, column, value))

    def or_where_greater_than_or_equals(self, column: IdentifierLike, value: Value) -> Self:
        """``OR`` twin of :meth:`where_greater_than_or_equals`."""
        return self._add_where(Condition(ConditionEnum.GREATER_THAN_OR_EQUALS, column, value, ChainEnum.OR))

    def where_between(self, column: IdentifierLike, min: Value, max: Value) -> Self:
        """``column BETWEEN min AND max``."""
        return self._add_where(Condition(ConditionEnum.BETWEEN, column, (min, max)))

    def or_where_between(self, column: IdentifierLike, min: Value, max: Value) -> Self:
        """``OR`` twin of :meth:`where_between`."""
        return self._add_where(Condition(ConditionEnum.BETWEEN, column, (min, max), ChainEnum.OR))

    def where_not_between(self, column: IdentifierLike, min: Value, max: Value) -> Self:
        """``column NOT BETWEEN min AND max``."""
        return self._add_where(Condition(ConditionEnum.NOT_BETWEEN, column, (min, max)))

    def or_where_not_between(self, column: IdentifierLike, min: Value, max: Value) -> Self:
        """``OR`` twin of :meth:`where_not_between`."""
        return self._add_where(Condition(ConditionEnum.NOT_BETWEEN, column, (min, max), ChainEnum.OR))

    def where_empty(self, column: IdentifierLike) -> Self:
        """``column`` is ``NULL``, ``0`` or ``''``. For text, numeric and boolean columns only: date and time columns
        cannot be compared with ``'0'`` (an error on PostgreSQL, a wrong answer on MySQL)."""
        return self._add_where(_conditions.empty(column, ChainEnum.AND))

    def or_where_empty(self, column: IdentifierLike) -> Self:
        """``OR`` twin of :meth:`where_empty`."""
        return self._add_where(_conditions.empty(column, ChainEnum.OR))

    def where_not_empty(self, column: IdentifierLike) -> Self:
        """``column`` is not ``NULL``, ``0`` or ``''``; text, numeric and boolean columns only
        (see :meth:`where_empty`)."""
        return self._add_where(_conditions.not_empty(column, ChainEnum.AND))

    def or_where_not_empty(self, column: IdentifierLike) -> Self:
        """``OR`` twin of :meth:`where_not_empty`."""
        return self._add_where(_conditions.not_empty(column, ChainEnum.OR))

    def where_regex(self, column: IdentifierLike, pattern: str, flags: str = "") -> Self:
        """``column`` matches the regular expression ``pattern``; ``flags`` such as ``"i"``."""
        return self._add_where(Condition(ConditionEnum.REGEX, column, pattern, flags=flags))

    def or_where_regex(self, column: IdentifierLike, pattern: str, flags: str = "") -> Self:
        """``OR`` twin of :meth:`where_regex`."""
        return self._add_where(Condition(ConditionEnum.REGEX, column, pattern, ChainEnum.OR, flags=flags))

    def where_not_regex(self, column: IdentifierLike, pattern: str, flags: str = "") -> Self:
        """``column`` does not match the regular expression ``pattern``."""
        return self._add_where(Condition(ConditionEnum.NOT_REGEX, column, pattern, flags=flags))

    def or_where_not_regex(self, column: IdentifierLike, pattern: str, flags: str = "") -> Self:
        """``OR`` twin of :meth:`where_not_regex`."""
        return self._add_where(Condition(ConditionEnum.NOT_REGEX, column, pattern, ChainEnum.OR, flags=flags))

    def where_exists(self, select_query: SelectQuery) -> Self:
        """``EXISTS (<select>)``."""
        return self._add_where(Condition(ConditionEnum.EXISTS, None, select_query))

    def or_where_exists(self, select_query: SelectQuery) -> Self:
        """``OR`` twin of :meth:`where_exists`."""
        return self._add_where(Condition(ConditionEnum.EXISTS, None, select_query, ChainEnum.OR))

    def where_not_exists(self, select_query: SelectQuery) -> Self:
        """``NOT EXISTS (<select>)``."""
        return self._add_where(Condition(ConditionEnum.NOT_EXISTS, None, select_query))

    def or_where_not_exists(self, select_query: SelectQuery) -> Self:
        """``OR`` twin of :meth:`where_not_exists`."""
        return self._add_where(Condition(ConditionEnum.NOT_EXISTS, None, select_query, ChainEnum.OR))

    def where_group(self, callback: Callable[[WhereGroup], WhereGroup | None]) -> Self:
        """``(<conditions>)``: ``callback`` fills a fresh :class:`WhereGroup`. An empty group is left out."""
        return self._add_where_group(callback, ChainEnum.AND, negate=False)

    def or_where_group(self, callback: Callable[[WhereGroup], WhereGroup | None]) -> Self:
        """``OR`` twin of :meth:`where_group`."""
        return self._add_where_group(callback, ChainEnum.OR, negate=False)

    def where_not_group(self, callback: Callable[[WhereGroup], WhereGroup | None]) -> Self:
        """``NOT (<conditions>)``; see :meth:`where_group`."""
        return self._add_where_group(callback, ChainEnum.AND, negate=True)

    def or_where_not_group(self, callback: Callable[[WhereGroup], WhereGroup | None]) -> Self:
        """``OR`` twin of :meth:`where_not_group`."""
        return self._add_where_group(callback, ChainEnum.OR, negate=True)

    def where_operator(
        self, column: IdentifierLike, operator: str | ConditionEnum, value: Value | Sequence[Value]
    ) -> Self:
        """``column <operator> value`` with the operator emitted verbatim; a sequence renders ``(?, ?, ..)``."""
        return self._add_where(Condition(str(operator), column, value))

    def or_where_operator(
        self, column: IdentifierLike, operator: str | ConditionEnum, value: Value | Sequence[Value]
    ) -> Self:
        """``OR`` twin of :meth:`where_operator`."""
        return self._add_where(Condition(str(operator), column, value, ChainEnum.OR))

    def wheref(self, format: str, *values: Value) -> Self:
        """A printf style condition, e.g. ``wheref("%s > %d", identifier("age"), 18)``; see :class:`ExpressionF`."""
        return self._add_where(Condition(ConditionEnum.RAW, None, ExpressionF(format, values)))

    def or_wheref(self, format: str, *values: Value) -> Self:
        """``OR`` twin of :meth:`wheref`."""
        return self._add_where(Condition(ConditionEnum.RAW, None, ExpressionF(format, values), ChainEnum.OR))

    def where(self, sql: str, values: Sequence[Scalar] | Mapping[str, Scalar] = ()) -> Self:
        """A raw ``(<sql>)`` condition with ``?`` placeholders and positional values, or ``:name`` placeholders
        and a mapping."""
        return self._add_where(_conditions.raw_condition(sql, values, ChainEnum.AND))

    def or_where(self, sql: str, values: Sequence[Scalar] | Mapping[str, Scalar] = ()) -> Self:
        """``OR`` twin of :meth:`where`."""
        return self._add_where(_conditions.raw_condition(sql, values, ChainEnum.OR))

    def _add_where(self, condition: ConditionNode) -> Self:
        self._where += (condition,)

        return self

    def _add_where_group(
        self, callback: Callable[[WhereGroup], WhereGroup | None], chain: ChainEnum, negate: bool
    ) -> Self:
        from ..objects.condition_groups import WhereGroup  # circular: WhereGroup is built on this mixin

        group = _conditions.group(WhereGroup(), callback, chain, negate)

        return self if group is None else self._add_where(group)


__all__ = ["WhereMixin"]
