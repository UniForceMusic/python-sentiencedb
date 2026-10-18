"""Aliased identifiers and aliased sub-selects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..query import Query
    from ..type_aliases import IdentifierLike


@dataclass(frozen=True, slots=True)
class Alias:
    """``<identifier> AS <alias>``, e.g. ``alias(["u", "name"], "user_name")``."""

    identifier: IdentifierLike
    alias: str


@dataclass(frozen=True, slots=True)
class SubQuery:
    """``(<select>) AS <alias>``: a sub-select used as a table or a select list entry."""

    select_query: Query
    alias: str


__all__ = ["Alias", "SubQuery"]
