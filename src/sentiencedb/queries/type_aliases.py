"""Type aliases shared by builders, value objects and dialects.

They only exist for the type checker; never use them in ``isinstance`` checks.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from .objects.alias import Alias, SubQuery
    from .objects.sql import Sql
    from .query import Query

Scalar: TypeAlias = bool | int | float | Decimal | str | bytes | date | datetime | None
"""A value that can be bound as a statement parameter (PHP ``null|bool|int|float|string|DateTimeInterface``)."""

IdentifierLike: TypeAlias = "str | Sequence[str | Sql] | Sql"
"""An identifier: ``"name"``, a qualified path ``["schema", "table"]`` (each part escaped and joined with ``.``),
or any :class:`Sql` (rendered verbatim through ``raw_sql``). A dotted string is *not* split."""

Value: TypeAlias = "Scalar | Sql | Query"
"""Anything that can stand where a value goes: a bound scalar, inline SQL, or a sub-select."""

ConditionValue: TypeAlias = "Value | Sequence[Value]"
"""The ``value`` of a :class:`~sentiencedb.queries.objects.Condition`; see its docstring for the shape per kind."""

TableRef: TypeAlias = "IdentifierLike | Alias | SubQuery"
"""What a ``SELECT`` reads from or a join targets."""

SelectColumn: TypeAlias = "IdentifierLike | Alias | SubQuery | Query"
"""A select list entry. A bare :class:`Query` renders as ``(<sub-select>)``."""

__all__ = ["ConditionValue", "IdentifierLike", "Scalar", "SelectColumn", "TableRef", "Value"]
