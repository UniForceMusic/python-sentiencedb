"""``INSERT`` builder (with native or emulated upserts and ``RETURNING``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Self

from ..driver import Driver
from ..exceptions import QueryError
from ..results.result import Result
from .mixins.returning_mixin import ReturningMixin
from .objects.clauses import OnConflict
from .objects.sql import Sql
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

    ``RETURNING`` means the same on every database and path (PostgreSQL's semantics): the rows the statement inserted
    or updated come back with exactly the ``returning`` columns, and a row skipped by :meth:`on_conflict_do_nothing`
    returns nothing.

    Upserts and ``RETURNING`` run natively where the dialect supports them. Otherwise (or when asked to with
    :meth:`emulate_on_conflict` / :meth:`emulate_returning`) :meth:`execute` emulates them with extra statements:

    * An emulated upsert runs inside :meth:`DatabaseABC.transaction` (a savepoint inside an open transaction). Per row
      it selects the existing row by the conflict columns, then inserts, updates or skips it. A row with a ``NULL``
      conflict value never conflicts, as with a native unique key. The emulation is atomic but not race safe: a row
      another connection inserts between the select and the insert raises :class:`UniqueViolationError`.
    * Emulated ``RETURNING`` selects each inserted row back by its explicit :meth:`last_insert_id` column value, by
      its conflict columns (in an upsert) or by :meth:`DatabaseABC.last_insert_id`, and an updated row by its id.
    * A native upsert does not say which row it touched, so emulated ``RETURNING`` makes the upsert emulated too. So
      does ``DO NOTHING`` with ``RETURNING`` on MySQL and MariaDB, whose ``ON DUPLICATE KEY UPDATE`` would return the
      skipped row. The ``updates`` of an emulated upsert must then be valid in a plain ``UPDATE``.
    """

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        super().__init__(database, dialect)
        self._table = table
        self._values: tuple[Mapping[str, Value], ...] = ()
        self._on_conflict: OnConflict | None = None
        self._last_insert_id: str | None = None
        self._emulate_on_conflict = False
        self._emulate_returning = False

    def values(self, *rows: Mapping[str, Value]) -> Self:
        """Append rows (column -> value). Columns missing from a row are inserted as ``DEFAULT``."""
        self._values += tuple(dict(row) for row in rows)

        return self

    def on_conflict_do_nothing(self, conflict: str | Sequence[str]) -> Self:
        """Skip rows that conflict on the given columns, or on the constraint named by a ``str``."""
        self._on_conflict = OnConflict(_conflict(conflict))

        return self

    def insert_ignore(self, conflict: str | Sequence[str]) -> Self:
        """Alias of :meth:`on_conflict_do_nothing`."""
        return self.on_conflict_do_nothing(conflict)

    def on_conflict_do_update(self, conflict: str | Sequence[str], updates: Mapping[str, Value] | None = None) -> Self:
        """Update rows that conflict on the given columns (or named constraint) with ``updates``; without updates
        every inserted column is overwritten with its new value."""
        self._on_conflict = OnConflict(_conflict(conflict), {} if updates is None else dict(updates))

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

    def emulate_on_conflict(self, last_insert_id: str) -> Self:
        """Emulate the upsert even when the dialect supports it natively; it runs inside
        :meth:`DatabaseABC.transaction`.

        :param last_insert_id: The auto increment column.
        """
        self._emulate_on_conflict = True
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

        Every row is validated before any statement runs.

        :raises QueryError: There are no rows; an emulated upsert targets a named constraint or a row lacks a
            conflict column; emulated ``RETURNING`` has no :meth:`last_insert_id` column to find an inserted row by;
            or more than one existing row matches a row's conflict columns.
        """
        if not self._values:
            raise QueryError("no insert values specified")

        on_conflict = self._on_conflict

        if on_conflict is None or not self._emulates_on_conflict():
            self._validate_returning(None)

            return self._insert(self._values, emulate_prepare)

        conflict = self._validate_upsert(on_conflict)
        self._validate_returning(conflict)

        return self._database.transaction(
            lambda _: _merge(
                [self._upsert(conflict, on_conflict.updates, row, emulate_prepare) for row in self._values]
            )
        )

    def _emulates_on_conflict(self) -> bool:
        if self._emulate_on_conflict or not self._dialect.on_conflict:
            return True

        if self._returning is None or self._on_conflict is None:
            return False

        do_nothing = self._on_conflict.updates is None

        return self._emulates_returning() or (do_nothing and self._dialect.driver in (Driver.MYSQL, Driver.MARIADB))

    def _emulates_returning(self) -> bool:
        return self._emulate_returning or not self._dialect.returning

    def _build(self, rows: Sequence[Mapping[str, Value]]) -> QueryWithParams:
        return self._dialect.insert(
            table=self._table,
            values=rows,
            on_conflict=None if self._emulates_on_conflict() else self._on_conflict,
            returning=None if self._emulates_returning() else self._returning,
            last_insert_id=self._last_insert_id,
        )

    def _validate_upsert(self, on_conflict: OnConflict) -> tuple[str, ...]:
        """The conflict columns, after checking that every row holds them."""
        if isinstance(on_conflict.conflict, str):
            raise QueryError(
                f"an emulated upsert needs conflict columns, not the constraint name {on_conflict.conflict!r}"
            )

        conflict = tuple(on_conflict.conflict)

        if not conflict:
            raise QueryError("an emulated upsert needs at least one conflict column")

        if any(column not in row for row in self._values for column in conflict):
            raise QueryError("insert values does not contain constraint columns")

        return conflict

    def _validate_returning(self, conflict: Sequence[str] | None) -> None:
        """Emulated ``RETURNING`` must be able to find every inserted row."""
        if self._returning is None or not self._emulates_returning() or self._last_insert_id is not None:
            return

        if conflict is None or any(row[column] is None for row in self._values for column in conflict):
            raise QueryError("emulated RETURNING needs last_insert_id(column) to select the inserted rows back")

    def _upsert(
        self,
        conflict_columns: Sequence[str],
        updates: Mapping[str, Value] | None,
        row: Mapping[str, Value],
        emulate_prepare: bool,
    ) -> ResultABC:
        conflict = {column: row[column] for column in conflict_columns}

        if any(value is None for value in conflict.values()):
            # A unique key never matches NULL, so the row cannot conflict.
            return self._insert_row(row, None, emulate_prepare)

        key_columns = [self._last_insert_id] if self._last_insert_id is not None else list(conflict)
        existing = self._select(conflict, 2, key_columns, emulate_prepare).fetch_dicts()

        if not existing:
            return self._insert_row(row, conflict, emulate_prepare)

        if len(existing) > 1:
            raise QueryError("multiple rows in constraint")

        if updates is None:
            return self._skipped()

        if self._last_insert_id is not None:
            key: dict[str, Value] = {self._last_insert_id: next(iter(existing[0].values()))}
        else:
            key = conflict

        return self._update(updates or row, key, emulate_prepare)

    def _insert(self, rows: Sequence[Mapping[str, Value]], emulate_prepare: bool) -> ResultABC:
        if len(rows) == 1:
            return self._insert_row(rows[0], None, emulate_prepare)

        if self._returning is None or not self._emulates_returning():
            return self._database.query_with_params(self._build(rows), emulate_prepare)

        # Each row is inserted (and selected back) on its own, as an id identifies a single row.
        return self._database.transaction(
            lambda _: _merge([self._insert_row(row, None, emulate_prepare) for row in rows])
        )

    def _insert_row(
        self, row: Mapping[str, Value], conflict: Mapping[str, Value] | None, emulate_prepare: bool
    ) -> ResultABC:
        """Insert one row; with emulated ``RETURNING`` select it back by the explicit id, ``conflict`` (its non
        ``NULL`` conflict values) or the generated id, in that order."""
        result = self._database.query_with_params(self._build((row,)), emulate_prepare)

        if self._returning is None or not self._emulates_returning():
            return result

        key = self._explicit_id(row) or conflict

        if key is None and self._last_insert_id is not None:
            last_insert_id = self._database.last_insert_id()
            key = {self._last_insert_id: last_insert_id} if last_insert_id else None

        if key is None:
            return result

        return self._select(key, 1, self._returning, emulate_prepare)

    def _explicit_id(self, row: Mapping[str, Value]) -> dict[str, Value] | None:
        """``{id column: value}`` when the row holds a plain value for the :meth:`last_insert_id` column."""
        if self._last_insert_id is None:
            return None

        value = row.get(self._last_insert_id)

        return None if value is None or isinstance(value, Sql | Query) else {self._last_insert_id: value}

    def _update(self, updates: Mapping[str, Value], key: Mapping[str, Value], emulate_prepare: bool) -> ResultABC:
        update_query = UpdateQuery(self._database, self._dialect, self._table).set(updates)

        for column, value in key.items():
            update_query.where_equals(column, value)

        if self._returning is None:
            return update_query.execute(emulate_prepare)

        if not self._emulates_returning() and self._dialect.update_returning:
            return update_query.returning(self._returning).execute(emulate_prepare)

        update_query.execute(emulate_prepare)

        # The update may have changed the key columns themselves.
        updated_key = {
            column: updates[column] if column in updates and not isinstance(updates[column], Sql | Query) else value
            for column, value in key.items()
        }

        return self._select(updated_key, 1, self._returning, emulate_prepare)

    def _skipped(self) -> Result:
        """The result of a row ``DO NOTHING`` skipped: no rows, like PostgreSQL's ``RETURNING``."""
        return Result(dict.fromkeys(self._returning or ()))

    def _select(
        self, conditions: Mapping[str, Value], limit: int, columns: Sequence[str], emulate_prepare: bool
    ) -> ResultABC:
        select_query = SelectQuery(self._database, self._dialect, self._table).columns(list(columns)).limit(limit)

        for column, value in conditions.items():
            select_query.where_equals(column, value)

        return select_query.execute(emulate_prepare)


def _conflict(conflict: str | Sequence[str]) -> str | tuple[str, ...]:
    """A constraint name, or a copy of the conflict columns."""
    return conflict if isinstance(conflict, str) else tuple(conflict)


def _merge(results: Sequence[ResultABC]) -> Result:
    """One in-memory result with the rows of all ``results``; a column keeps the first type any result reports."""
    columns: dict[str, str | None] = {}
    rows: list[dict[str, object]] = []

    for result in results:
        for column, type in result.columns().items():
            if columns.get(column) is None:
                columns[column] = type

        rows += result.fetch_dicts()

    return Result(columns, rows)


__all__ = ["InsertQuery"]
