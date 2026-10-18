"""The dialect interface and server version parsing."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..driver import Driver
    from ..options import Options
    from ..queries.enums import TypeEnum
    from ..queries.objects.alias import Alias
    from ..queries.objects.clauses import ConditionNode, JoinClause, OnConflict, OrderBy, UnionClause
    from ..queries.objects.ddl import Alter, Column, TableConstraint
    from ..queries.objects.query_with_params import QueryWithParams
    from ..queries.objects.sql import Sql
    from ..queries.type_aliases import IdentifierLike, Scalar, SelectColumn, TableRef, Value

_VERSION_PATTERN = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
_MARIADB_PREFIX = re.compile(r"^\s*5\.5\.5-(?=\d)")


def parse_version(version: int | str) -> int:
    """Encode a server version as ``major * 10000 + minor * 100 + patch``.

    Integers are taken as already encoded. Strings are parsed from their first ``major[.minor[.patch]]`` run, so
    vendor suffixes are ignored: ``"3.45.1"`` -> ``34501``, ``"16.2 (Debian 16.2-1)"`` -> ``160200``,
    ``"8.0.34"`` -> ``80034``, ``"10.11.6-MariaDB-1:10.11.6+maria"`` -> ``101106``. Unparseable input gives ``0``.

    MariaDB before 11.0 reports itself as ``5.5.5-<version>`` on the wire (a replication compatibility prefix); the
    prefix is dropped, so ``"5.5.5-10.11.6-MariaDB"`` -> ``101106``.
    """
    if isinstance(version, int):
        return version

    match = _VERSION_PATTERN.search(_MARIADB_PREFIX.sub("", version))

    if match is None:
        return 0

    major, minor, patch = (int(part or 0) for part in match.groups())

    return major * 10000 + minor * 100 + patch


class DialectABC(ABC):
    """Generates SQL for one database engine and converts values to and from it.

    Dialects are pure: they never touch a connection. Statement methods take the builder state as keyword arguments
    and return a :class:`QueryWithParams` whose ``query`` uses ``?`` placeholders and whose ``params`` is a list in
    placeholder order. Adapters convert the placeholders to their driver's paramstyle.

    :param driver: The engine; MySQL and MariaDB share a dialect class and differ by this value.
    :param version: Server version, raw (``"16.2"``) or encoded; see :func:`parse_version`.
    :param options: Connection options; dialects read their own keys (``use_regexp``, ``use_serials``, ...).
    """

    def __init__(self, driver: Driver, version: int | str = 0, options: Options | None = None) -> None:
        self.driver = driver
        self.version = parse_version(version)
        self.options: Options = options or {}

    # -- statements ---------------------------------------------------------------------------------------------

    @abstractmethod
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
        """``SELECT [DISTINCT [ON (..)]] <columns> FROM <table> [joins] [WHERE] [GROUP BY] [HAVING] [ORDER BY]
        [LIMIT] [OFFSET]``; with unions the select is parenthesised and each ``UNION [ALL] (<select>)`` appended.

        :param columns: Empty selects ``*``.
        :param distinct: ``None``: no ``DISTINCT``; empty: ``DISTINCT``; columns: ``DISTINCT ON (..)``.
        :param offset: Only emitted when ``limit`` is set.
        :raises QueryError: ``DISTINCT ON`` or ``LATERAL`` used but unsupported.
        """

    @abstractmethod
    def insert(
        self,
        *,
        table: IdentifierLike,
        values: Sequence[Mapping[str, Value]],
        on_conflict: OnConflict | None = None,
        returning: Sequence[str] | None = None,
        last_insert_id: str | None = None,
    ) -> QueryWithParams:
        """``INSERT INTO <table> (<columns>) VALUES (..), (..) [ON CONFLICT ..] [RETURNING ..]``.

        The column list is the union of every row's keys in first seen order; a row missing a column gets
        ``DEFAULT``. ``on_conflict`` is ignored when the dialect does not support it, as is ``returning``
        (builders emulate both). ``last_insert_id`` names the auto increment column (MySQL upserts use it to make
        ``LAST_INSERT_ID()`` return the existing row's id).

        :param returning: ``None``: no ``RETURNING``; empty: ``RETURNING *``.
        :raises QueryError: No values, or an unsupported conflict target (e.g. a named constraint on SQLite).
        """

    @abstractmethod
    def update(
        self,
        *,
        table: IdentifierLike,
        updates: Mapping[str, Value],
        where: Sequence[ConditionNode] = (),
        returning: Sequence[str] | None = None,
    ) -> QueryWithParams:
        """``UPDATE <table> SET <column> = <value>, .. [WHERE] [RETURNING]``.

        :raises QueryError: No updates, or ``returning`` without :attr:`update_returning` support.
        """

    @abstractmethod
    def delete(
        self,
        *,
        table: IdentifierLike,
        where: Sequence[ConditionNode] = (),
        returning: Sequence[str] | None = None,
    ) -> QueryWithParams:
        """``DELETE FROM <table> [WHERE] [RETURNING]``.

        :raises QueryError: ``returning`` without :attr:`returning` support.
        """

    @abstractmethod
    def create_table(
        self,
        *,
        table: IdentifierLike,
        columns: Sequence[Column],
        primary_keys: Sequence[str] = (),
        constraints: Sequence[TableConstraint] = (),
        if_not_exists: bool = False,
    ) -> QueryWithParams:
        """``CREATE TABLE [IF NOT EXISTS] <table> (<columns>, PRIMARY KEY (..), <constraints>)``.

        ``IF NOT EXISTS`` is only emitted when :attr:`table_exists` is true.

        :raises QueryError: No columns.
        """

    @abstractmethod
    def alter_table(self, *, table: IdentifierLike, alters: Sequence[Alter]) -> list[QueryWithParams]:
        """One ``ALTER TABLE <table> <operation>`` statement per alter, in order.

        :raises QueryError: No alters, or an operation the dialect cannot express.
        """

    @abstractmethod
    def drop_table(self, *, table: IdentifierLike, if_exists: bool = False) -> QueryWithParams:
        """``DROP TABLE [IF EXISTS] <table>`` (``IF EXISTS`` only when :attr:`table_exists`)."""

    @abstractmethod
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
        """``CREATE [UNIQUE] INDEX [IF NOT EXISTS] <name> ON <table> (<columns>) [WHERE ..]``.

        ``IF NOT EXISTS`` is only emitted when :attr:`index_exists` is true; ``where`` makes a partial index.

        :raises QueryError: No columns.
        """

    @abstractmethod
    def drop_index(self, *, table: IdentifierLike, name: str, if_exists: bool = False) -> QueryWithParams:
        """``DROP INDEX [IF EXISTS] <name>`` (``... ON <table>`` on MySQL)."""

    @abstractmethod
    def begin_transaction(self) -> QueryWithParams:
        """Statement that starts a transaction."""

    @abstractmethod
    def commit_transaction(self) -> QueryWithParams:
        """Statement that commits the current transaction."""

    @abstractmethod
    def rollback_transaction(self) -> QueryWithParams:
        """Statement that rolls back the current transaction."""

    @abstractmethod
    def begin_savepoint(self, name: str) -> QueryWithParams:
        """``SAVEPOINT <name>``."""

    @abstractmethod
    def commit_savepoint(self, name: str) -> QueryWithParams:
        """``RELEASE SAVEPOINT <name>``."""

    @abstractmethod
    def rollback_savepoint(self, name: str) -> QueryWithParams:
        """``ROLLBACK TO <name>``."""

    # -- escaping and value conversion --------------------------------------------------------------------------

    @abstractmethod
    def escape_identifier(self, identifier: IdentifierLike | Alias) -> str:
        """Quote an identifier. Paths are escaped per part and joined with ``.``; :class:`Sql` renders its
        ``raw_sql``; :class:`Alias` renders ``<identifier> AS <alias>``."""

    @abstractmethod
    def escape_string(self, string: str) -> str:
        """Quote a string literal."""

    @abstractmethod
    def cast_to_driver(self, value: Scalar) -> Scalar:
        """Convert a value before it is bound as a parameter (bools and dates as the dialect stores them)."""

    @abstractmethod
    def cast_to_query(self, value: Scalar) -> str:
        """Render a value as a SQL literal (emulated prepares, debug output, column defaults).

        :raises QueryError: The value has no literal form (``inf``, ``nan``).
        """

    @abstractmethod
    def cast_bool(self, value: bool) -> bool | int:
        """A bool as the dialect stores it: ``True``/``False`` with native booleans, else ``1``/``0``."""

    @abstractmethod
    def cast_datetime(self, value: datetime) -> str:
        """A datetime in the dialect's literal format."""

    @abstractmethod
    def parse_bool(self, value: Scalar) -> bool:
        """Interpret a stored bool (``1``, ``"t"``, ``True``, ...)."""

    @abstractmethod
    def parse_datetime(self, string: str) -> datetime | None:
        """Parse a stored datetime; ``None`` when it is not one."""

    @abstractmethod
    def type(self, type: TypeEnum, size: int | None = None) -> str:
        """The SQL column type for a portable :class:`TypeEnum`; ``size`` as documented on :class:`Type`."""

    # -- feature flags ------------------------------------------------------------------------------------------

    @property
    @abstractmethod
    def boolean(self) -> bool:
        """Native ``BOOLEAN`` type and ``TRUE``/``FALSE`` literals."""

    @property
    @abstractmethod
    def distinct_on(self) -> bool:
        """``SELECT DISTINCT ON (..)``."""

    @property
    @abstractmethod
    def generated_by_default_as_identity(self) -> bool:
        """``GENERATED BY DEFAULT AS IDENTITY`` columns."""

    @property
    @abstractmethod
    def index_exists(self) -> bool:
        """``CREATE INDEX IF NOT EXISTS`` / ``DROP INDEX IF EXISTS``."""

    @property
    @abstractmethod
    def lateral(self) -> bool:
        """``LATERAL`` joins."""

    @property
    @abstractmethod
    def on_conflict(self) -> bool:
        """Native upserts (``ON CONFLICT`` / ``ON DUPLICATE KEY UPDATE``)."""

    @property
    @abstractmethod
    def returning(self) -> bool:
        """``RETURNING`` clauses on ``INSERT`` and ``DELETE``."""

    @property
    @abstractmethod
    def update_returning(self) -> bool:
        """``RETURNING`` clauses on ``UPDATE``."""

    @property
    @abstractmethod
    def savepoints(self) -> bool:
        """``SAVEPOINT`` statements."""

    @property
    @abstractmethod
    def table_exists(self) -> bool:
        """``CREATE TABLE IF NOT EXISTS`` / ``DROP TABLE IF EXISTS``."""


__all__ = ["DialectABC", "parse_version"]
