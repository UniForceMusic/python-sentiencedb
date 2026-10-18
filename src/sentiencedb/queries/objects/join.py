"""The ``ON`` builder handed to join callbacks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from ..mixins.where_mixin import WhereMixin
from .clauses import JoinClause
from .sql import Identifier

if TYPE_CHECKING:
    from ..enums import JoinEnum
    from ..type_aliases import TableRef


class Join(WhereMixin):
    """One join of a select; its ``where_*`` methods build the ``ON`` clause, e.g.
    ``db.select("users").left_join("orders", lambda join: join.on(["orders", "user_id"], ["users", "id"]))``."""

    def __init__(self, join: JoinEnum, table: TableRef) -> None:
        self._join = join
        self._table = table

    def on(self, join: Sequence[str], on: Sequence[str]) -> Self:
        """``join = on``, comparing two (qualified) columns."""
        return self.where_equals(join, Identifier(on))

    def or_on(self, join: Sequence[str], on: Sequence[str]) -> Self:
        """``OR`` twin of :meth:`on`."""
        return self.or_where_equals(join, Identifier(on))

    def to_join_clause(self) -> JoinClause:
        """The join as dialect input."""
        return JoinClause(self._join, self._table, self._where)


__all__ = ["Join"]
