"""MySQL and MariaDB dialect."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar

from ..driver import Driver
from ..exceptions import QueryError
from ..queries.enums import TypeEnum
from ..queries.objects.ddl import AddColumn, AddPrimaryKeys, Type
from ..queries.objects.query_with_params import QueryWithParams
from ..queries.objects.sql import CurrentTimestamp, Raw
from .sql_dialect import SQLDialect

if TYPE_CHECKING:
    from ..queries.objects.clauses import Condition, ConditionNode, OnConflict
    from ..queries.objects.ddl import Alter, AlterColumn, Column, DropConstraint
    from ..queries.type_aliases import ConditionValue, IdentifierLike, Scalar, Value


class MySQLDialect(SQLDialect):
    """MySQL (``Driver.MYSQL``) and MariaDB (``Driver.MARIADB``).

    Identifiers are quoted with backticks and string literals with ``'``, so ``ANSI_QUOTES`` does not matter. String
    literals double the quote and backslash escape the special characters: under ``NO_BACKSLASH_ESCAPES`` such a
    literal keeps its backslashes, but it can never end early.
    """

    DATETIME_FORMAT: ClassVar[str] = "%Y-%m-%d %H:%M:%S.%f"
    ESCAPE_IDENTIFIER: ClassVar[str] = "`"
    ESCAPE_STRING: ClassVar[str] = "'"
    ESCAPE_CHARS: ClassVar[Mapping[str, str]] = {
        "\\": "\\\\",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\0": "\\0",
        "\b": "\\b",
        "\x1a": "\\Z",
    }
    BOOLEAN: ClassVar[bool] = False
    DROP_INDEX_ON_TABLE: ClassVar[bool] = True
    GENERATED_BY_DEFAULT_AS_IDENTITY: ClassVar[bool] = False

    @property
    def mariadb(self) -> bool:
        """Whether this dialect targets MariaDB rather than MySQL."""
        return self.driver == Driver.MARIADB

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
        """Like :meth:`SQLDialect.create_index`, but MySQL and MariaDB have no partial indexes.

        :raises QueryError: ``where`` is not empty.
        """
        if where:
            raise QueryError("MySQL and MariaDB do not support partial indexes")

        return super().create_index(
            table=table, name=name, columns=columns, unique=unique, if_not_exists=if_not_exists, where=where
        )

    def alter_table(self, *, table: IdentifierLike, alters: Sequence[Alter]) -> list[QueryWithParams]:
        """Like :meth:`SQLDialect.alter_table`, but an ``AUTO_INCREMENT`` column is added in one statement with the
        primary key that follows it: MySQL requires the column to be a key the moment it is added."""
        if not alters:
            raise QueryError("no table alters specified")

        statements: list[list[str]] = []

        for index, alter in enumerate(alters):
            previous = alters[index - 1] if index else None
            clause = self._build_alter(alter)

            if (
                isinstance(alter, AddPrimaryKeys)
                and isinstance(previous, AddColumn)
                and previous.generated_by_default_as_identity
            ):
                statements[-1].append(clause)
            else:
                statements.append([clause])

        table_sql = self.escape_identifier(table)

        return [QueryWithParams(f"ALTER TABLE {table_sql} {', '.join(clauses)}") for clauses in statements]

    def begin_transaction(self) -> QueryWithParams:
        return QueryWithParams("START TRANSACTION")

    def _cast_type(self, value: ConditionValue) -> str | None:
        match value:
            case bool():
                return "UNSIGNED"
            case int():
                return "SIGNED"
            case float():
                # A bare DECIMAL is DECIMAL(10, 0), which rounds to an integer.
                return "DOUBLE" if self.version >= (100405 if self.mariadb else 80017) else "DECIMAL(65, 30)"
            case str():
                return "CHAR"
            case _:
                return super()._cast_type(value)

    def _build_condition_like(self, params: list[Scalar], condition: Condition) -> str:
        """``LIKE BINARY`` when case sensitive. Case insensitive ``LIKE`` compares ``lower()`` of the column converted
        to ``utf8mb4``, so it also ignores case on ``_bin``/``_cs`` collations and binary columns, where ``lower()``
        alone does nothing."""
        if self.version < 40000:
            return super()._build_condition_like(params, condition)

        column = self._build_column_name(condition)
        value = self._build_value(params, condition.value)
        negation = self._negation(condition)

        if condition.case_insensitive:
            return f"lower(convert({column} USING utf8mb4)) {negation}LIKE lower({value})"

        return f"{column} {negation}LIKE BINARY {value}"

    def _build_condition_regex(self, params: list[Scalar], condition: Condition) -> str:
        """``regexp_like()`` on MySQL >= 8.0 unless the ``use_regexp`` option is set; otherwise ``REGEXP``.

        Both follow the column's (usually case insensitive) collation, so matching is made case sensitive unless the
        flags contain ``i``: ``regexp_like()`` gets match type ``c`` before the flags (with ``s`` spelled ``n``),
        ``REGEXP`` a ``(?-i)`` prefix (MariaDB and MySQL >= 8.0). The match type is inlined as a literal: MySQL
        rejects a bound one ("Invalid use of parameters").

        :raises QueryError: An unsupported flag; on MySQL < 8.0, any flag but ``s`` (``.`` always matches a newline
            there, and the collation decides the case sensitivity).
        """
        flags = self._regex_flags(condition.flags)

        if not self.mariadb and self.version >= 80000 and not self.options.get("use_regexp", False):
            column = self._build_column_name(condition)
            pattern = self._build_value(params, condition.value)
            match_type = self.cast_to_query("c" + flags.replace("s", "n"))

            return f"{self._negation(condition)}regexp_like({column}, {pattern}, {match_type})"

        if self.mariadb or self.version >= 80000:
            inline = flags if "i" in flags else f"{flags}-i"
        elif set(flags) - {"s"}:
            raise QueryError("MySQL before 8.0 supports no regex flags but s")
        else:
            inline = ""

        return self._build_condition_regex_operator(params, condition, "REGEXP", "NOT REGEXP", inline)

    def _build_on_conflict(
        self,
        params: list[Scalar],
        on_conflict: OnConflict | None,
        columns: Sequence[str],
        last_insert_id: str | None,
    ) -> str:
        """``ON DUPLICATE KEY UPDATE ..``; the conflict target is implied by the table's unique keys.

        Empty updates set every inserted column to its new ``values()``. With ``last_insert_id`` the column is also
        set to ``last_insert_id(<column>)``, so ``LAST_INSERT_ID()`` returns the id of an existing row. ``DO NOTHING``
        sets a column to itself (the first conflict column, else the first inserted one): unlike ``INSERT IGNORE``
        this only reacts to duplicate keys and keeps every other error an error.
        """
        if on_conflict is None or not self.on_conflict:
            return ""

        updates: dict[str, Value] = {}

        if on_conflict.updates is not None:
            updates = dict(on_conflict.updates) or {
                column: Raw(f"values({self.escape_identifier(column)})") for column in columns
            }

        if last_insert_id:
            updates[last_insert_id] = Raw(f"last_insert_id({self.escape_identifier(last_insert_id)})")
        elif not updates:
            conflict = on_conflict.conflict
            targets = [*conflict] if conflict and not isinstance(conflict, str) else [*columns]

            if not targets:
                raise QueryError("ON DUPLICATE KEY UPDATE needs a conflict column or an inserted column")

            updates[targets[0]] = Raw(self.escape_identifier(targets[0]))

        return f"ON DUPLICATE KEY UPDATE {self._build_set(params, updates)}"

    def _build_column(self, column: Column) -> str:
        """Identity columns get ``AUTO_INCREMENT``; ``DEFAULT CURRENT_TIMESTAMP`` on ``DATETIME(p)`` gets the
        column's precision, as MySQL requires."""
        column_type = column.type

        if (
            isinstance(column_type, Type)
            and column_type.type is TypeEnum.DATETIME
            and column_type.size
            and isinstance(column.default, CurrentTimestamp)
        ):
            column = replace(column, default=CurrentTimestamp(column_type.size))

        sql = super()._build_column(column)

        return f"{sql} AUTO_INCREMENT" if column.generated_by_default_as_identity else sql

    def _primary_key_columns(self, columns: Sequence[Column], primary_keys: Sequence[str]) -> Sequence[str]:
        """``AUTO_INCREMENT`` columns must be keys, so identity columns join the primary key."""
        identities = [column.name for column in columns if column.generated_by_default_as_identity]

        return list(dict.fromkeys([*primary_keys, *identities]))

    def _build_alter_column(self, alter: AlterColumn) -> str:
        return f"MODIFY {self.escape_identifier(alter.column)} {alter.sql}"

    def _build_drop_constraint(self, alter: DropConstraint) -> str:
        """``DROP CONSTRAINT`` (any kind) on MySQL >= 8.0.19 and MariaDB >= 10.5; before, ``DROP INDEX`` (unique
        constraints only)."""
        if self.version >= (100500 if self.mariadb else 80019):
            return super()._build_drop_constraint(alter)

        return f"DROP INDEX {self.escape_identifier(alter.constraint)}"

    def type(self, type: TypeEnum, size: int | None = None) -> str:
        bits = size or 0

        match type:
            case TypeEnum.BOOLEAN:
                # The display width is what tells a boolean from an integer when the table is introspected.
                return "TINYINT(1)"
            case TypeEnum.FLOAT:
                return "DOUBLE" if bits > 32 else "FLOAT"
            case TypeEnum.TEXT:
                if bits > 16777215:
                    return "LONGTEXT"
                if bits > 65535:
                    return "MEDIUMTEXT"
                return "TEXT"
            case TypeEnum.DATETIME:
                return f"DATETIME({bits})" if bits > 0 else "DATETIME"
            case _:
                return super().type(type, size)

    @property
    def lateral(self) -> bool:
        return not self.mariadb and self.version >= 80014

    @property
    def on_conflict(self) -> bool:
        return self.mariadb or self.version >= 40100

    @property
    def returning(self) -> bool:
        return self.mariadb and self.version >= 100500

    @property
    def update_returning(self) -> bool:
        """MariaDB supports ``RETURNING`` on ``INSERT`` and ``DELETE`` only."""
        return False

    @property
    def table_exists(self) -> bool:
        return self.version >= 32300


__all__ = ["MySQLDialect"]
