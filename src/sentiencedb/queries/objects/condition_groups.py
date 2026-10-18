"""Nested condition groups handed to ``where_group`` / ``having_group`` callbacks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..mixins.having_mixin import HavingMixin
from ..mixins.where_mixin import WhereMixin

if TYPE_CHECKING:
    from .clauses import ConditionNode


class WhereGroup(WhereMixin):
    """Collects the conditions of a parenthesised ``WHERE`` group: ``db.select("t").where_group(lambda group:
    group.where_equals("a", 1).or_where_equals("b", 2))``."""

    @property
    def conditions(self) -> tuple[ConditionNode, ...]:
        """The conditions added so far."""
        return self._where


class HavingGroup(HavingMixin):
    """Collects the conditions of a parenthesised ``HAVING`` group; see :class:`WhereGroup`."""

    @property
    def conditions(self) -> tuple[ConditionNode, ...]:
        """The conditions added so far."""
        return self._having


__all__ = ["HavingGroup", "WhereGroup"]
