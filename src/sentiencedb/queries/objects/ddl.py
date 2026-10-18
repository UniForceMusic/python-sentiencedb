"""Plain data describing table structure, shared by DDL builders and schema introspection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from ..enums import ReferentialActionEnum, TypeEnum

if TYPE_CHECKING:
    from ..type_aliases import IdentifierLike, Scalar
    from .sql import Sql


@dataclass(frozen=True, slots=True)
class Type:
    """A portable column type, rendered by :meth:`DialectABC.type`.

    ``size`` means bits for ``INTEGER``/``FLOAT`` (> 32 selects the 64 bit type), the length for ``VARCHAR``
    (default 255), the maximum length for ``TEXT`` (MySQL picks ``MEDIUMTEXT``/``LONGTEXT`` from it) and the
    fractional second digits for ``DATETIME``.
    """

    type: TypeEnum
    size: int | None = None


@dataclass(frozen=True, slots=True)
class Column:
    """A column definition (``CREATE TABLE``, ``ADD COLUMN``) or an introspected column.

    :param type: A portable :class:`Type`, or a raw SQL type string emitted verbatim.
    :param default: ``None`` means no ``DEFAULT`` clause; a :class:`Sql` is inlined; anything else becomes a literal.
    :param generated_by_default_as_identity: Auto incrementing column (identity, ``SERIAL``, ``AUTO_INCREMENT`` or
        ``INTEGER PRIMARY KEY AUTOINCREMENT`` depending on the dialect).
    """

    name: str
    type: str | Type
    not_null: bool = False
    default: Scalar | Sql = None
    generated_by_default_as_identity: bool = False


@dataclass(frozen=True, slots=True)
class UniqueConstraint:
    """``[CONSTRAINT <name>] UNIQUE (<columns>)``."""

    columns: Sequence[str]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ForeignKeyConstraint:
    """``[CONSTRAINT <name>] FOREIGN KEY (<columns>) REFERENCES <table> (<columns>) [ON UPDATE ..] [ON DELETE ..]``.

    Referential actions may be given as raw strings for actions without an enum member.
    """

    columns: Sequence[str]
    reference_table: IdentifierLike
    reference_columns: Sequence[str]
    name: str | None = None
    on_update: ReferentialActionEnum | str | None = None
    on_delete: ReferentialActionEnum | str | None = None


@dataclass(frozen=True, slots=True)
class Index:
    """An introspected index."""

    name: str
    columns: Sequence[str]
    unique: bool = False


@dataclass(frozen=True, slots=True)
class AddColumn(Column):
    """``ADD COLUMN <column definition>``."""


@dataclass(frozen=True, slots=True)
class AlterColumn:
    """``ALTER COLUMN <column> <sql>`` (``MODIFY <column> <sql>`` on MySQL)."""

    column: str
    sql: str


@dataclass(frozen=True, slots=True)
class RenameColumn:
    """``RENAME COLUMN <old> TO <new>``."""

    old: str
    new: str


@dataclass(frozen=True, slots=True)
class DropColumn:
    """``DROP COLUMN <column>``."""

    column: str


@dataclass(frozen=True, slots=True)
class AddPrimaryKeys:
    """``ADD PRIMARY KEY (<columns>)``."""

    columns: Sequence[str]


@dataclass(frozen=True, slots=True)
class AddUniqueConstraint(UniqueConstraint):
    """``ADD [CONSTRAINT <name>] UNIQUE (<columns>)``."""


@dataclass(frozen=True, slots=True)
class AddForeignKeyConstraint(ForeignKeyConstraint):
    """``ADD [CONSTRAINT <name>] FOREIGN KEY ...``."""


@dataclass(frozen=True, slots=True)
class DropConstraint:
    """``DROP CONSTRAINT <constraint>`` (``DROP INDEX <constraint>`` on MySQL)."""

    constraint: str


Alter: TypeAlias = (
    "AddColumn | AlterColumn | RenameColumn | DropColumn | AddPrimaryKeys | AddUniqueConstraint"
    " | AddForeignKeyConstraint | DropConstraint | Sql"
)
"""One ``ALTER TABLE`` operation. A :class:`Sql` is emitted verbatim after ``ALTER TABLE <table> ``."""

TableConstraint: TypeAlias = "UniqueConstraint | ForeignKeyConstraint | Sql"
"""A table constraint inside ``CREATE TABLE``. A :class:`Sql` is emitted verbatim."""


__all__ = [
    "AddColumn",
    "AddForeignKeyConstraint",
    "AddPrimaryKeys",
    "AddUniqueConstraint",
    "Alter",
    "AlterColumn",
    "Column",
    "DropColumn",
    "DropConstraint",
    "ForeignKeyConstraint",
    "Index",
    "RenameColumn",
    "TableConstraint",
    "Type",
    "UniqueConstraint",
]
