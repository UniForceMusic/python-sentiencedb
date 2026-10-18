"""Generic ANSI-ish dialect; the base of every concrete dialect."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar

from ..exceptions import QueryError
from ..queries.enums import TypeEnum
from ..queries.objects.alias import Alias
from ..queries.objects.query_with_params import QueryWithParams
from ..queries.objects.sql import Sql
from .dialect_abc import DialectABC

if TYPE_CHECKING:
    from ..queries.objects.clauses import ConditionNode, JoinClause, OnConflict, OrderBy, UnionClause
    from ..queries.objects.ddl import Alter, Column, TableConstraint
    from ..queries.type_aliases import IdentifierLike, Scalar, SelectColumn, TableRef, Value

_TRUE_STRINGS = frozenset({"true", "t", "yes", "y", "on"})


class SQLDialect(DialectABC):
    """Generic SQL generation shared by all dialects.

    Engine differences are expressed through the class constants below, through the feature flag properties
    (version gated in subclasses) and by overriding the protected ``_build_*`` hooks.
    """

    DATETIME_FORMAT: ClassVar[str] = "%Y-%m-%d %H:%M:%S"
    """``strftime`` format of datetime parameters and literals."""
    ESCAPE_ANSI: ClassVar[bool] = True
    """Escape the string quote by doubling it (``''``); otherwise by a backslash (``\\'``)."""
    ESCAPE_IDENTIFIER: ClassVar[str] = '"'
    ESCAPE_STRING: ClassVar[str] = "'"
    ESCAPE_CHARS: ClassVar[Mapping[str, str]] = {"\0": ""}
    """Extra replacements applied inside string literals (never inside identifiers)."""
    BOOLEAN: ClassVar[bool] = True
    DISTINCT_ON: ClassVar[bool] = False
    DROP_INDEX_ON_TABLE: ClassVar[bool] = False
    GENERATED_BY_DEFAULT_AS_IDENTITY: ClassVar[bool] = True
    INDEX_EXISTS: ClassVar[bool] = False
    LATERAL: ClassVar[bool] = True
    ON_CONFLICT: ClassVar[bool] = False
    RETURNING: ClassVar[bool] = False
    SAVEPOINTS: ClassVar[bool] = True
    TABLE_EXISTS: ClassVar[bool] = False

    # -- statements (Phase 2, Agent A) --------------------------------------------------------------------------

    def select(
        self,
        *,
        table: TableRef,
        columns: Sequence[SelectColumn] = (),
        distinct: Sequence[IdentifierLike] | None = None,
        joins: Sequence[JoinClause | Sql] = (),
        where: Sequence[ConditionNode] = (),
        group_by: Sequence[IdentifierLike] = (),
        having: Sequence[ConditionNode] = (),
        order_by: Sequence[OrderBy] = (),
        limit: int | None = None,
        offset: int | None = None,
        unions: Sequence[UnionClause] = (),
    ) -> QueryWithParams:
        raise NotImplementedError

    def insert(
        self,
        *,
        table: IdentifierLike,
        values: Sequence[Mapping[str, Value]],
        on_conflict: OnConflict | None = None,
        returning: Sequence[str] | None = None,
        last_insert_id: str | None = None,
    ) -> QueryWithParams:
        raise NotImplementedError

    def update(
        self,
        *,
        table: IdentifierLike,
        updates: Mapping[str, Value],
        where: Sequence[ConditionNode] = (),
        returning: Sequence[str] | None = None,
    ) -> QueryWithParams:
        raise NotImplementedError

    def delete(
        self,
        *,
        table: IdentifierLike,
        where: Sequence[ConditionNode] = (),
        returning: Sequence[str] | None = None,
    ) -> QueryWithParams:
        raise NotImplementedError

    def create_table(
        self,
        *,
        table: IdentifierLike,
        columns: Sequence[Column],
        primary_keys: Sequence[str] = (),
        constraints: Sequence[TableConstraint] = (),
        if_not_exists: bool = False,
    ) -> QueryWithParams:
        raise NotImplementedError

    def alter_table(self, *, table: IdentifierLike, alters: Sequence[Alter]) -> list[QueryWithParams]:
        raise NotImplementedError

    def drop_table(self, *, table: IdentifierLike, if_exists: bool = False) -> QueryWithParams:
        raise NotImplementedError

    def create_index(
        self,
        *,
        table: IdentifierLike,
        name: str,
        columns: Sequence[str],
        unique: bool = False,
        if_not_exists: bool = False,
        where: Sequence[ConditionNode] = (),
    ) -> QueryWithParams:
        raise NotImplementedError

    def drop_index(self, *, table: IdentifierLike, name: str, if_exists: bool = False) -> QueryWithParams:
        raise NotImplementedError

    # -- transactions -------------------------------------------------------------------------------------------

    def begin_transaction(self) -> QueryWithParams:
        return QueryWithParams("BEGIN TRANSACTION")

    def commit_transaction(self) -> QueryWithParams:
        return QueryWithParams("COMMIT")

    def rollback_transaction(self) -> QueryWithParams:
        return QueryWithParams("ROLLBACK")

    def begin_savepoint(self, name: str) -> QueryWithParams:
        return QueryWithParams(f"SAVEPOINT {self.escape_identifier(name)}")

    def commit_savepoint(self, name: str) -> QueryWithParams:
        return QueryWithParams(f"RELEASE SAVEPOINT {self.escape_identifier(name)}")

    def rollback_savepoint(self, name: str) -> QueryWithParams:
        return QueryWithParams(f"ROLLBACK TO {self.escape_identifier(name)}")

    # -- escaping -----------------------------------------------------------------------------------------------

    def escape_identifier(self, identifier: IdentifierLike | Alias) -> str:
        if isinstance(identifier, Alias):
            return f"{self.escape_identifier(identifier.identifier)} AS {self.escape_identifier(identifier.alias)}"

        if isinstance(identifier, Sql):
            return identifier.raw_sql(self)

        if isinstance(identifier, str):
            quote = self.ESCAPE_IDENTIFIER

            return quote + identifier.replace("\0", "").replace(quote, quote * 2) + quote

        return ".".join(self.escape_identifier(part) for part in identifier)

    def escape_string(self, string: str) -> str:
        return self.ESCAPE_STRING + string.translate(self._string_translation) + self.ESCAPE_STRING

    @cached_property
    def _string_translation(self) -> dict[int, str]:
        quote = self.ESCAPE_STRING
        replacements = {**self.ESCAPE_CHARS, quote: quote * 2 if self.ESCAPE_ANSI else "\\" + quote}

        return str.maketrans(replacements)

    # -- value conversion ---------------------------------------------------------------------------------------

    def cast_to_driver(self, value: Scalar) -> Scalar:
        if isinstance(value, bool):
            return self.cast_bool(value)

        if isinstance(value, datetime):
            return self.cast_datetime(value)

        if isinstance(value, date):
            return value.isoformat()

        return value

    def cast_to_query(self, value: Scalar) -> str:
        if value is None:
            return "NULL"

        if isinstance(value, bool):
            if self.boolean:
                return "TRUE" if value else "FALSE"

            return str(int(value))

        if isinstance(value, int):
            return str(value)

        if isinstance(value, float):
            if not math.isfinite(value):
                raise QueryError(f"{value!r} has no SQL literal")

            return repr(value)

        if isinstance(value, Decimal):
            if not value.is_finite():
                raise QueryError(f"{value!r} has no SQL literal")

            return str(value)

        if isinstance(value, str):
            return self.escape_string(value)

        if isinstance(value, bytes):
            return self._bytes_literal(value)

        if isinstance(value, datetime):
            return self.escape_string(self.cast_datetime(value))

        return self.escape_string(value.isoformat())

    def _bytes_literal(self, value: bytes) -> str:
        """A binary literal; the SQL standard ``X'..'`` form by default."""
        return f"X'{value.hex()}'"

    def cast_bool(self, value: bool) -> bool | int:
        return value if self.boolean else int(value)

    def cast_datetime(self, value: datetime) -> str:
        return value.strftime(self.DATETIME_FORMAT)

    def parse_bool(self, value: Scalar) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, int | float | Decimal):
            return value > 0

        if isinstance(value, bytes):
            return int.from_bytes(value, "big") > 0

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in _TRUE_STRINGS:
                return True

            try:
                return float(normalized) > 0
            except ValueError:
                return False

        return value is not None

    def parse_datetime(self, string: str) -> datetime | None:
        try:
            return datetime.strptime(string, self.DATETIME_FORMAT)
        except ValueError:
            pass

        try:
            return datetime.fromisoformat(string)
        except ValueError:
            return None

    def type(self, type: TypeEnum, size: int | None = None) -> str:
        bits = size or 0

        match type:
            case TypeEnum.BOOLEAN:
                return "BOOLEAN" if self.boolean else "INTEGER"
            case TypeEnum.INTEGER:
                return "BIGINT" if bits > 32 else "INTEGER"
            case TypeEnum.FLOAT:
                return "DECIMAL(30, 15)" if bits > 32 else "DECIMAL(15, 7)"
            case TypeEnum.VARCHAR:
                return f"VARCHAR({255 if size is None else size})"
            case TypeEnum.TEXT:
                return "TEXT"
            case TypeEnum.DATETIME:
                return "DATETIME"

    # -- feature flags ------------------------------------------------------------------------------------------

    @property
    def boolean(self) -> bool:
        return self.BOOLEAN

    @property
    def distinct_on(self) -> bool:
        return self.DISTINCT_ON

    @property
    def generated_by_default_as_identity(self) -> bool:
        return self.GENERATED_BY_DEFAULT_AS_IDENTITY

    @property
    def index_exists(self) -> bool:
        return self.INDEX_EXISTS

    @property
    def lateral(self) -> bool:
        return self.LATERAL

    @property
    def on_conflict(self) -> bool:
        return self.ON_CONFLICT

    @property
    def returning(self) -> bool:
        return self.RETURNING

    @property
    def savepoints(self) -> bool:
        return self.SAVEPOINTS

    @property
    def table_exists(self) -> bool:
        return self.TABLE_EXISTS


__all__ = ["SQLDialect"]
