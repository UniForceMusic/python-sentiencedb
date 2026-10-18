"""``ALTER TABLE`` builder: one statement per operation."""

from __future__ import annotations

import builtins
import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Self

from .enums import TypeEnum
from .helpers import column_names
from .objects.ddl import (
    AddColumn,
    AddForeignKeyConstraint,
    AddPrimaryKeys,
    AddUniqueConstraint,
    AlterColumn,
    DropColumn,
    DropConstraint,
    RenameColumn,
    Type,
)
from .objects.sql import Raw

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..results.result_abc import ResultABC
    from .enums import ReferentialActionEnum
    from .objects.ddl import Alter
    from .objects.query_with_params import QueryWithParams
    from .objects.sql import Sql
    from .type_aliases import IdentifierLike, Scalar


class AlterTableQuery:
    """``ALTER TABLE <table> <operation>``. Compiles to one statement per operation, so it is not a :class:`Query`.

    Columns are added with :meth:`add_column` and the portable helpers (:meth:`add_identity`, :meth:`add_boolean`,
    :meth:`add_integer`, :meth:`add_float`, :meth:`add_varchar`, :meth:`add_text`, :meth:`add_datetime`).
    """

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        self._database = database
        self._dialect = dialect
        self._table = table
        self._alters: tuple[Alter, ...] = ()

    def add_column(
        self,
        name: str,
        type: str | Type,
        not_null: bool = False,
        default: Scalar | Sql = None,
        generated_by_default_as_identity: bool = False,
    ) -> Self:
        """``ADD COLUMN``; ``type`` is a portable :class:`Type` or a raw SQL type."""
        return self._add(AddColumn(name, type, not_null, default, generated_by_default_as_identity))

    def alter_column(self, column: str, sql: str) -> Self:
        """``ALTER COLUMN column <sql>`` (``MODIFY`` on MySQL), e.g. ``alter_column("name", "TYPE TEXT")``."""
        return self._add(AlterColumn(column, sql))

    def rename_column(self, old: str, new: str) -> Self:
        """``RENAME COLUMN old TO new``."""
        return self._add(RenameColumn(old, new))

    def drop_column(self, column: str) -> Self:
        """``DROP COLUMN column``."""
        return self._add(DropColumn(column))

    def add_primary_keys(self, columns: str | Sequence[str]) -> Self:
        """``ADD PRIMARY KEY (columns)``."""
        return self._add(AddPrimaryKeys(column_names(columns)))

    def add_unique_constraint(self, columns: str | Sequence[str], name: str | None = None) -> Self:
        """``ADD [CONSTRAINT name] UNIQUE (columns)``."""
        return self._add(AddUniqueConstraint(column_names(columns), name))

    def add_foreign_key_constraint(
        self,
        columns: str | Sequence[str],
        reference_table: IdentifierLike,
        reference_columns: str | Sequence[str],
        name: str | None = None,
        on_update: ReferentialActionEnum | str | None = None,
        on_delete: ReferentialActionEnum | str | None = None,
    ) -> Self:
        """``ADD [CONSTRAINT name] FOREIGN KEY (columns) REFERENCES reference_table (reference_columns)``."""
        return self._add(
            AddForeignKeyConstraint(
                column_names(columns), reference_table, column_names(reference_columns), name, on_update, on_delete
            )
        )

    def drop_constraint(self, constraint: str) -> Self:
        """``DROP CONSTRAINT constraint`` (``DROP INDEX`` on MySQL)."""
        return self._add(DropConstraint(constraint))

    def alter(self, sql: str) -> Self:
        """A raw operation, emitted verbatim after ``ALTER TABLE <table>``."""
        return self._add(Raw(sql))

    def add_identity(self, name: str, bits: int = 64, add_primary_key: bool = True) -> Self:
        """Add an auto incrementing ``NOT NULL`` integer column, followed by ``ADD PRIMARY KEY`` on it."""
        self.add_integer(name, bits, not_null=True, generated_by_default_as_identity=True)

        return self.add_primary_keys(name) if add_primary_key else self

    def add_auto_increment(self, name: str, bits: int = 64, add_primary_key: bool = True) -> Self:
        """Alias of :meth:`add_identity`."""
        return self.add_identity(name, bits, add_primary_key)

    def add_boolean(self, name: str, not_null: bool = False, default: bool | Sql | None = None) -> Self:
        """Add a ``BOOLEAN`` column."""
        return self.add_column(name, Type(TypeEnum.BOOLEAN), not_null, default)

    def add_integer(
        self,
        name: str,
        bits: int = 64,
        not_null: bool = False,
        default: int | Sql | None = None,
        generated_by_default_as_identity: bool = False,
    ) -> Self:
        """Add an integer column; more than 32 ``bits`` selects ``BIGINT``."""
        return self.add_column(name, Type(TypeEnum.INTEGER, bits), not_null, default, generated_by_default_as_identity)

    def add_float(
        self, name: str, bits: int = 64, not_null: bool = False, default: builtins.float | Decimal | Sql | None = None
    ) -> Self:
        """Add a floating point column; more than 32 ``bits`` selects double precision."""
        return self.add_column(name, Type(TypeEnum.FLOAT, bits), not_null, default)

    def add_varchar(
        self, name: str, length: int = 255, not_null: bool = False, default: str | Sql | None = None
    ) -> Self:
        """Add a ``VARCHAR(length)`` column."""
        return self.add_column(name, Type(TypeEnum.VARCHAR, length), not_null, default)

    def add_text(
        self, name: str, length: int | None = None, not_null: bool = False, default: str | Sql | None = None
    ) -> Self:
        """Add a ``TEXT`` column; ``length`` is the maximum length the column must hold."""
        return self.add_column(name, Type(TypeEnum.TEXT, length), not_null, default)

    def add_datetime(
        self, name: str, precision: int = 6, not_null: bool = False, default: dt.datetime | Sql | None = None
    ) -> Self:
        """Add a date and time column with ``precision`` fractional second digits where supported."""
        return self.add_column(name, Type(TypeEnum.DATETIME, precision), not_null, default)

    def to_query_with_params(self) -> list[QueryWithParams]:
        """Compile to one statement per operation."""
        return self._dialect.alter_table(table=self._table, alters=self._alters)

    def to_sql(self) -> list[str]:
        """The statements with every parameter interpolated as a literal."""
        return [query_with_params.to_sql(self._dialect) for query_with_params in self.to_query_with_params()]

    def execute(self, emulate_prepare: bool = False) -> list[ResultABC]:
        """Run the statements in order, returning one result per operation."""
        return [
            self._database.query_with_params(query_with_params, emulate_prepare)
            for query_with_params in self.to_query_with_params()
        ]

    def _add(self, alter: Alter) -> Self:
        self._alters += (alter,)

        return self


__all__ = ["AlterTableQuery"]
