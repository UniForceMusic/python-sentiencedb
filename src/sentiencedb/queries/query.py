"""Base class of the single-statement query builders."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..results.result_abc import ResultABC
    from .objects.query_with_params import QueryWithParams


class Query(ABC):
    """A builder that compiles to exactly one statement.

    Builders only hold state; :meth:`to_query_with_params` hands that state to the bound dialect, which produces
    the SQL. Any ``Query`` used as a *value* (in a condition, a select list, ``SET``, ``UNION``, ...) is rendered by
    the dialect as a parenthesised sub-select whose parameters are merged into the outer statement.
    """

    def __init__(self, database: DatabaseABC, dialect: DialectABC) -> None:
        self._database = database
        self._dialect = dialect

    @abstractmethod
    def to_query_with_params(self) -> QueryWithParams:
        """Compile to SQL with ``?`` placeholders plus the parameters to bind."""

    def to_sql(self) -> str:
        """Compile to SQL with every parameter interpolated as a literal (for logging and debugging)."""
        return self.to_query_with_params().to_sql(self._dialect)

    def execute(self, emulate_prepare: bool = False) -> ResultABC:
        """Run the statement on the bound database.

        :param emulate_prepare: Interpolate parameters client side instead of using a server side prepared statement.
        """
        return self._database.query_with_params(self.to_query_with_params(), emulate_prepare)


__all__ = ["Query"]
