"""``CREATE TABLE`` builder."""

from __future__ import annotations

import builtins
import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Self

from ..results.result import Result
from ._ddl import column_names
from ._introspection import table_exists
from .enums import TypeEnum
from .mixins.if_not_exists_mixin import IfNotExistsMixin
from .objects.ddl import Column, ForeignKeyConstraint, Type, UniqueConstraint
from .objects.sql import Raw
from .query import Query

if TYPE_CHECKING:
    from ..database_abc import DatabaseABC
    from ..dialects.dialect_abc import DialectABC
    from ..results.result_abc import ResultABC
    from .enums import ReferentialActionEnum
    from .objects.ddl import TableConstraint
    from .objects.query_with_params import QueryWithParams
    from .objects.sql import Sql
    from .type_aliases import IdentifierLike, Scalar


class CreateTableQuery(IfNotExistsMixin, Query):
    """``CREATE TABLE [IF NOT EXISTS] <table> (<columns>, PRIMARY KEY (..), <constraints>)``.

    Columns are added with :meth:`column` and the portable helpers (:meth:`identity`, :meth:`boolean`,
    :meth:`integer`, :meth:`float`, :meth:`varchar`, :meth:`text`, :meth:`datetime`).
    """

    def __init__(self, database: DatabaseABC, dialect: DialectABC, table: IdentifierLike) -> None:
        super().__init__(database, dialect)
        self._table = table
        self._columns: tuple[Column, ...] = ()
        self._primary_keys: tuple[str, ...] = ()
        self._constraints: tuple[TableConstraint, ...] = ()

    def column(
        self,
        name: str,
        type: str | Type,
        not_null: bool = False,
        default: Scalar | Sql = None,
        generated_by_default_as_identity: bool = False,
    ) -> Self:
        """Add a column; ``type`` is a portable :class:`Type` or a raw SQL type."""
        self._columns += (Column(name, type, not_null, default, generated_by_default_as_identity),)

        return self

    def identity(self, name: str, bits: int = 64, primary_key: bool = True) -> Self:
        """Add an auto incrementing ``NOT NULL`` integer column, by default as (part of) the primary key."""
        if primary_key and name not in self._primary_keys:
            self._primary_keys += (name,)

        return self.integer(name, bits, not_null=True, generated_by_default_as_identity=True)

    def auto_increment(self, name: str, bits: int = 64, primary_key: bool = True) -> Self:
        """Alias of :meth:`identity`."""
        return self.identity(name, bits, primary_key)

    def boolean(self, name: str, not_null: bool = False, default: bool | Sql | None = None) -> Self:
        """Add a ``BOOLEAN`` column."""
        return self.column(name, Type(TypeEnum.BOOLEAN), not_null, default)

    def integer(
        self,
        name: str,
        bits: int = 64,
        not_null: bool = False,
        default: int | Sql | None = None,
        generated_by_default_as_identity: bool = False,
    ) -> Self:
        """Add an integer column; more than 32 ``bits`` selects ``BIGINT``."""
        return self.column(name, Type(TypeEnum.INTEGER, bits), not_null, default, generated_by_default_as_identity)

    def float(
        self, name: str, bits: int = 64, not_null: bool = False, default: builtins.float | Decimal | Sql | None = None
    ) -> Self:
        """Add a floating point column; more than 32 ``bits`` selects double precision."""
        return self.column(name, Type(TypeEnum.FLOAT, bits), not_null, default)

    def varchar(self, name: str, length: int = 255, not_null: bool = False, default: str | Sql | None = None) -> Self:
        """Add a ``VARCHAR(length)`` column."""
        return self.column(name, Type(TypeEnum.VARCHAR, length), not_null, default)

    def text(
        self, name: str, length: int | None = None, not_null: bool = False, default: str | Sql | None = None
    ) -> Self:
        """Add a ``TEXT`` column; ``length`` is the maximum length the column must hold."""
        return self.column(name, Type(TypeEnum.TEXT, length), not_null, default)

    def datetime(
        self, name: str, precision: int = 6, not_null: bool = False, default: dt.datetime | Sql | None = None
    ) -> Self:
        """Add a date and time column with ``precision`` fractional second digits where supported."""
        return self.column(name, Type(TypeEnum.DATETIME, precision), not_null, default)

    def primary_keys(self, columns: str | Sequence[str]) -> Self:
        """Replace the primary key columns."""
        self._primary_keys = column_names(columns)

        return self

    def unique_constraint(self, columns: Sequence[str], name: str | None = None) -> Self:
        """Add ``[CONSTRAINT name] UNIQUE (columns)``."""
        self._constraints += (UniqueConstraint(tuple(columns), name),)

        return self

    def foreign_key_constraint(
        self,
        columns: str | Sequence[str],
        reference_table: IdentifierLike,
        reference_columns: str | Sequence[str],
        name: str | None = None,
        on_update: ReferentialActionEnum | str | None = None,
        on_delete: ReferentialActionEnum | str | None = None,
    ) -> Self:
        """Add ``[CONSTRAINT name] FOREIGN KEY (columns) REFERENCES reference_table (reference_columns)``."""
        self._constraints += (
            ForeignKeyConstraint(
                column_names(columns), reference_table, column_names(reference_columns), name, on_update, on_delete
            ),
        )

        return self

    def constraint(self, sql: str) -> Self:
        """Add a raw table constraint, emitted verbatim."""
        self._constraints += (Raw(sql),)

        return self

    def to_query_with_params(self) -> QueryWithParams:
        return self._dialect.create_table(
            table=self._table,
            columns=self._columns,
            primary_keys=self._primary_keys,
            constraints=self._constraints,
            if_not_exists=self._if_not_exists and not self._emulate_if_not_exists,
        )

    def execute(self, emulate_prepare: bool = False) -> ResultABC:
        """Create the table. An emulated ``IF NOT EXISTS`` returns an empty :class:`Result` for an existing table."""
        emulate = self._emulate_if_not_exists or not self._dialect.table_exists

        if self._if_not_exists and emulate and table_exists(self._database, self._dialect, self._table):
            return Result()

        return super().execute(emulate_prepare)


__all__ = ["CreateTableQuery"]
