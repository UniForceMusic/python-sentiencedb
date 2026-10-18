"""``INSERT`` builder (with native or emulated upserts and ``RETURNING``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Self

from ..exceptions import QueryError
from ..results.result import Result
from .mixins.returning_mixin import ReturningMixin
from .objects.clauses import OnConflict
from .query import Query
from .select_query import SelectQuery
from .update_query import UpdateQuery

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..results.result_abc import ResultABC
    from .objects.query_with_params import QueryWithParams
    from .type_aliases import IdentifierLike, Value


class InsertQuery(ReturningMixin, Query):
    """``INSERT INTO <table> (<columns>) VALUES (..) [ON CONFLICT ..] [RETURNING ..]``.

    Upserts and ``RETURNING`` run natively where the dialect supports them. Otherwise (or when asked to with
    :meth:`emulate_on_conflict` / :meth:`emulate_returning`) :meth:`execute` emulates them with extra statements:
    an upsert selects each row by its conflict columns and then inserts, updates or skips it, and ``RETURNING``
    selects the inserted row back by its :meth:`last_insert_id` column.
    """

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        super().__init__(database, dialect)
        self._table = table
        self._values: tuple[Mapping[str, Value], ...] = ()
        self._on_conflict: OnConflict | None = None
        self._last_insert_id: str | None = None
        self._emulate_on_conflict = False
        self._emulate_on_conflict_in_transaction = False
        self._emulate_returning = False

    def values(self, *rows: Mapping[str, Value]) -> Self:
        """Append rows (column -> value). Columns missing from a row are inserted as ``DEFAULT``."""
        self._values += rows

        return self

    def on_conflict_do_nothing(self, conflict: str | Sequence[str]) -> Self:
        """Skip rows that conflict on the given columns, or on the constraint named by a ``str``."""
        self._on_conflict = OnConflict(conflict)

        return self

    def insert_ignore(self, conflict: str | Sequence[str]) -> Self:
        """Alias of :meth:`on_conflict_do_nothing`."""
        return self.on_conflict_do_nothing(conflict)

    def on_conflict_do_update(self, conflict: str | Sequence[str], updates: Mapping[str, Value] | None = None) -> Self:
        """Update rows that conflict on the given columns (or named constraint) with ``updates``; without updates
        every inserted column is overwritten with its new value."""
        self._on_conflict = OnConflict(conflict, {} if updates is None else updates)

        return self

    def on_duplicate_key_update(
        self, conflict: str | Sequence[str], updates: Mapping[str, Value] | None = None
    ) -> Self:
        """Alias of :meth:`on_conflict_do_update`."""
        return self.on_conflict_do_update(conflict, updates)

    def last_insert_id(self, column: str) -> Self:
        """Name the auto increment column, used to read back inserted rows and by MySQL upserts."""
        self._last_insert_id = column

        return self

    def emulate_on_conflict(self, last_insert_id: str, in_transaction: bool = False) -> Self:
        """Emulate the upsert even when the dialect supports it natively.

        :param last_insert_id: The auto increment column.
        :param in_transaction: Run the emulation's statements inside :meth:`DatabaseABC.transaction`.
        """
        self._emulate_on_conflict = True
        self._emulate_on_conflict_in_transaction = in_transaction
        self._last_insert_id = last_insert_id

        return self

    def emulate_returning(self, last_insert_id: str) -> Self:
        """Emulate ``RETURNING`` even when the dialect supports it natively.

        :param last_insert_id: The auto increment column the inserted row is selected back by.
        """
        self._emulate_returning = True
        self._last_insert_id = last_insert_id

        return self

    def to_query_with_params(self) -> QueryWithParams:
        return self._build(self._values)

    def execute(self, emulate_prepare: bool = False) -> ResultABC:
        """Run the insert, emulating the upsert and ``RETURNING`` where needed (see the class docstring).

        :raises QueryError: An emulated upsert targets a named constraint, a row lacks a conflict column, or more
            than one existing row matches a row's conflict columns.
        """
        on_conflict = self._on_conflict

        if on_conflict is None or not self._emulates_on_conflict():
            return self._insert(self._values, emulate_prepare)

        if self._emulate_on_conflict_in_transaction:
            return self._database.transaction(lambda _: self._upsert_rows(on_conflict, emulate_prepare))

        return self._upsert_rows(on_conflict, emulate_prepare)

    def _emulates_on_conflict(self) -> bool:
        return self._emulate_on_conflict or not self._dialect.on_conflict

    def _emulates_returning(self) -> bool:
        return self._emulate_returning or not self._dialect.returning

    def _build(self, rows: Sequence[Mapping[str, Value]]) -> QueryWithParams:
        return self._dialect.insert(
            table=self._table,
            values=rows,
            on_conflict=None if self._emulate_on_conflict else self._on_conflict,
            returning=None if self._emulate_returning else self._returning,
            last_insert_id=self._last_insert_id,
        )

    def _upsert_rows(self, on_conflict: OnConflict, emulate_prepare: bool) -> Result:
        columns: dict[str, str | None] = {}
        rows: list[dict[str, object]] = []

        for row in self._values:
            result = self._upsert(on_conflict, row, emulate_prepare)
            columns |= result.columns()
            rows += result.fetch_dicts()

        return Result(columns, rows)

    def _upsert(self, on_conflict: OnConflict, row: Mapping[str, Value], emulate_prepare: bool) -> ResultABC:
        if isinstance(on_conflict.conflict, str):
            raise QueryError("database does not support named constraints")

        if any(column not in row for column in on_conflict.conflict):
            raise QueryError("insert values does not contain constraint columns")

        conflict = {column: row[column] for column in on_conflict.conflict}
        result = self._select(conflict, 2, emulate_prepare)
        existing = result.fetch_dicts()

        if not existing:
            return self._insert((row,), emulate_prepare)

        if len(existing) > 1:
            raise QueryError("multiple rows in constraint")

        if on_conflict.updates is None:
            return Result(result.columns(), existing) if self._returning is not None else Result()

        return self._update(on_conflict.updates or row, conflict, emulate_prepare)

    def _insert(self, rows: Sequence[Mapping[str, Value]], emulate_prepare: bool) -> ResultABC:
        result = self._database.query_with_params(self._build(rows), emulate_prepare)

        if self._last_insert_id is None or self._returning is None or not self._emulates_returning():
            return result

        last_insert_id = self._database.last_insert_id()

        if not last_insert_id:
            return result

        return self._select({self._last_insert_id: last_insert_id}, 1, emulate_prepare)

    def _update(self, updates: Mapping[str, Value], conflict: Mapping[str, Value], emulate_prepare: bool) -> ResultABC:
        update_query = UpdateQuery(self._database, self._dialect, self._table).set(updates)

        for column, value in conflict.items():
            update_query.where_equals(column, value)

        if self._returning is not None:
            update_query.returning(self._returning)

        result = update_query.execute(emulate_prepare)

        if self._returning is None or not self._emulates_returning():
            return result

        return self._select(conflict, 1, emulate_prepare)

    def _select(self, conditions: Mapping[str, Value], limit: int, emulate_prepare: bool) -> ResultABC:
        columns = dict.fromkeys(filter(None, (self._last_insert_id, *self._returning))) if self._returning else {}
        select_query = SelectQuery(self._database, self._dialect, self._table).columns(list(columns)).limit(limit)

        for column, value in conditions.items():
            select_query.where_equals(column, value)

        if self._last_insert_id is not None:
            select_query.order_by_desc(self._last_insert_id)

        return select_query.execute(emulate_prepare)


__all__ = ["InsertQuery"]
