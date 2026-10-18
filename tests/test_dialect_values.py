"""The dialect value layer: version parsing, escaping, casting, types, feature flags and transaction SQL."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sentiencedb import Driver, QueryError, raw
from sentiencedb.dialects import MySQLDialect, PgSQLDialect, SQLDialect, SQLiteDialect, parse_version
from sentiencedb.queries.enums import TypeEnum
from sentiencedb.queries.objects import Alias
from sentiencedb.queries.type_aliases import Scalar


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("3.45.1", 34501),
        ("16.2", 160200),
        ("16.2 (Debian 16.2-1.pgdg120+2)", 160200),
        ("9.6.24", 90624),
        ("8.0.34", 80034),
        ("10.11.6-MariaDB-1:10.11.6+maria~ubu2204", 101106),
        ("18", 180000),
        ("", 0),
        ("unknown", 0),
        (80034, 80034),
    ],
)
def test_parse_version(version: int | str, expected: int) -> None:
    assert parse_version(version) == expected


def test_dialect_stores_driver_version_and_options() -> None:
    dialect = PgSQLDialect(Driver.PGSQL, "15.4", {"use_serials": True})

    assert dialect.driver is Driver.PGSQL
    assert dialect.version == 150400
    assert dialect.options == {"use_serials": True}
    assert MySQLDialect(Driver.MYSQL, 80034).options == {}


def test_driver_creates_matching_dialect() -> None:
    assert type(Driver.SQLITE.dialect("3.45.1")) is SQLiteDialect
    assert type(Driver.PGSQL.dialect("16.2")) is PgSQLDialect
    assert type(Driver.MYSQL.dialect("8.0.34")) is MySQLDialect
    assert Driver.MARIADB.dialect("11.4.4", {"use_regexp": True}).options == {"use_regexp": True}


class TestEscapeIdentifier:
    def test_quotes_and_doubles_the_quote_char(
        self, sqlite_dialect: SQLiteDialect, mysql_dialect: MySQLDialect
    ) -> None:
        assert sqlite_dialect.escape_identifier('we"ird') == '"we""ird"'
        assert mysql_dialect.escape_identifier("we`ird") == "`we``ird`"

    def test_never_applies_string_escapes(self, mysql_dialect: MySQLDialect, pgsql_dialect: PgSQLDialect) -> None:
        assert mysql_dialect.escape_identifier("a\\b\nc") == "`a\\b\nc`"
        assert pgsql_dialect.escape_identifier("a\\b") == '"a\\b"'

    def test_strips_nul(self, sqlite_dialect: SQLiteDialect) -> None:
        assert sqlite_dialect.escape_identifier("a\0b") == '"ab"'

    def test_path_alias_and_sql(self, sqlite_dialect: SQLiteDialect) -> None:
        assert sqlite_dialect.escape_identifier(["main", "users"]) == '"main"."users"'
        assert sqlite_dialect.escape_identifier("main.users") == '"main.users"'
        assert sqlite_dialect.escape_identifier(Alias(["u", "name"], "n")) == '"u"."name" AS "n"'
        assert sqlite_dialect.escape_identifier(raw("count(*)")) == "count(*)"


class TestEscapeString:
    def test_ansi_doubles_quote_and_strips_nul(self, sqlite_dialect: SQLiteDialect) -> None:
        assert sqlite_dialect.escape_string("it's\0 \\n") == "'it''s \\n'"

    def test_pgsql_keeps_backslashes_literal(self, pgsql_dialect: PgSQLDialect) -> None:
        assert pgsql_dialect.escape_string("a\\b'c\n") == "'a\\b''c\n'"

    def test_mysql_uses_backslash_escapes(self, mysql_dialect: MySQLDialect) -> None:
        assert mysql_dialect.escape_string("a'b\"c\\d\n\0\x1a") == "'a\\'b\"c\\\\d\\n\\0\\Z'"


class TestCastToDriver:
    def test_bools_follow_the_boolean_flag(self, pgsql_dialect: PgSQLDialect, sqlite_dialect: SQLiteDialect) -> None:
        assert pgsql_dialect.cast_to_driver(True) is True
        assert sqlite_dialect.cast_to_driver(True) == 1
        assert sqlite_dialect.cast_to_driver(False) == 0

    def test_datetimes_are_formatted(
        self, sqlite_dialect: SQLiteDialect, mysql_dialect: MySQLDialect, pgsql_dialect: PgSQLDialect
    ) -> None:
        moment = datetime(2024, 1, 2, 3, 4, 5, 678900)

        assert sqlite_dialect.cast_to_driver(moment) == "2024-01-02 03:04:05"
        assert mysql_dialect.cast_to_driver(moment) == "2024-01-02 03:04:05.678900"
        assert pgsql_dialect.cast_to_driver(moment) == "2024-01-02 03:04:05.678900"
        assert pgsql_dialect.cast_to_driver(moment.replace(tzinfo=UTC)) == "2024-01-02 03:04:05.678900+00:00"

    def test_dates_are_iso_formatted(self, sqlite_dialect: SQLiteDialect) -> None:
        assert sqlite_dialect.cast_to_driver(date(2024, 1, 2)) == "2024-01-02"

    @pytest.mark.parametrize("value", [None, 1, 1.5, Decimal("1.50"), "text", b"\x00\x01"])
    def test_other_values_pass_through(self, sqlite_dialect: SQLiteDialect, value: Scalar) -> None:
        assert sqlite_dialect.cast_to_driver(value) == value


class TestCastToQuery:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "NULL"),
            (True, "1"),
            (False, "0"),
            (42, "42"),
            (-7, "-7"),
            (1.5, "1.5"),
            (0.1, "0.1"),
            (1e-05, "1e-05"),
            (Decimal("12.50"), "12.50"),
            ("it's", "'it''s'"),
            (b"\xde\xad", "X'dead'"),
            (datetime(2024, 1, 2, 3, 4, 5), "'2024-01-02 03:04:05'"),
            (date(2024, 1, 2), "'2024-01-02'"),
        ],
    )
    def test_sqlite_literals(self, sqlite_dialect: SQLiteDialect, value: Scalar, expected: str) -> None:
        assert sqlite_dialect.cast_to_query(value) == expected

    def test_pgsql_literals(self, pgsql_dialect: PgSQLDialect) -> None:
        offset = timezone(timedelta(hours=2))

        assert pgsql_dialect.cast_to_query(True) == "TRUE"
        assert pgsql_dialect.cast_to_query(False) == "FALSE"
        assert pgsql_dialect.cast_to_query(b"\xde\xad") == "'\\xdead'::bytea"
        assert pgsql_dialect.cast_to_query(datetime(2024, 1, 2, tzinfo=offset)) == "'2024-01-02 00:00:00.000000+02:00'"

    def test_mysql_literals(self, mysql_dialect: MySQLDialect) -> None:
        assert mysql_dialect.cast_to_query(True) == "1"
        assert mysql_dialect.cast_to_query("a'b") == "'a\\'b'"
        assert mysql_dialect.cast_to_query(datetime(2024, 1, 2)) == "'2024-01-02 00:00:00.000000'"

    @pytest.mark.parametrize("value", [float("inf"), float("nan"), Decimal("NaN"), Decimal("-Infinity")])
    def test_non_finite_numbers_have_no_literal(self, sqlite_dialect: SQLiteDialect, value: float | Decimal) -> None:
        with pytest.raises(QueryError):
            sqlite_dialect.cast_to_query(value)


class TestParse:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            (2.5, True),
            (Decimal("0"), False),
            ("1", True),
            ("0", False),
            ("t", True),
            ("TRUE", True),
            ("false", False),
            ("no", False),
            (b"\x01", True),
            (b"\x00", False),
            (None, False),
        ],
    )
    def test_parse_bool(self, sqlite_dialect: SQLiteDialect, value: Scalar, expected: bool) -> None:
        assert sqlite_dialect.parse_bool(value) is expected

    def test_parse_datetime(self, sqlite_dialect: SQLiteDialect, pgsql_dialect: PgSQLDialect) -> None:
        assert sqlite_dialect.parse_datetime("2024-01-02 03:04:05") == datetime(2024, 1, 2, 3, 4, 5)
        assert sqlite_dialect.parse_datetime("2024-01-02T03:04:05.5") == datetime(2024, 1, 2, 3, 4, 5, 500000)
        assert pgsql_dialect.parse_datetime("2024-01-02 03:04:05.123456+02") == datetime(
            2024, 1, 2, 3, 4, 5, 123456, tzinfo=timezone(timedelta(hours=2))
        )
        assert sqlite_dialect.parse_datetime("not a date") is None


class TestType:
    @pytest.mark.parametrize(
        ("type_enum", "size", "sql", "mysql", "pgsql", "sqlite"),
        [
            (TypeEnum.BOOL, None, "BOOLEAN", "TINYINT", "BOOLEAN", "BOOLEAN"),
            (TypeEnum.INT, None, "INTEGER", "INTEGER", "INTEGER", "INTEGER"),
            (TypeEnum.INT, 32, "INTEGER", "INTEGER", "INTEGER", "INTEGER"),
            (TypeEnum.INT, 64, "BIGINT", "BIGINT", "BIGINT", "BIGINT"),
            (TypeEnum.FLOAT, 32, "DECIMAL(15, 7)", "FLOAT", "REAL", "REAL"),
            (TypeEnum.FLOAT, 64, "DECIMAL(30, 15)", "DOUBLE", "DOUBLE PRECISION", "REAL"),
            (TypeEnum.STRING, None, "VARCHAR(255)", "VARCHAR(255)", "VARCHAR(255)", "VARCHAR(255)"),
            (TypeEnum.STRING, 64, "VARCHAR(64)", "VARCHAR(64)", "VARCHAR(64)", "VARCHAR(64)"),
            (TypeEnum.STRING, 65535, "TEXT", "TEXT", "TEXT", "TEXT"),
            (TypeEnum.STRING, 65536, "TEXT", "MEDIUMTEXT", "TEXT", "TEXT"),
            (TypeEnum.STRING, 16777216, "TEXT", "LONGTEXT", "TEXT", "TEXT"),
            (TypeEnum.DATETIME, None, "DATETIME", "DATETIME", "TIMESTAMP", "DATETIME"),
            (TypeEnum.DATETIME, 6, "DATETIME", "DATETIME(6)", "TIMESTAMP", "DATETIME"),
        ],
    )
    def test_type_mapping(
        self,
        sql_dialect: SQLDialect,
        mysql_dialect: MySQLDialect,
        pgsql_dialect: PgSQLDialect,
        sqlite_dialect: SQLiteDialect,
        type_enum: TypeEnum,
        size: int | None,
        sql: str,
        mysql: str,
        pgsql: str,
        sqlite: str,
    ) -> None:
        assert sql_dialect.type(type_enum, size) == sql
        assert mysql_dialect.type(type_enum, size) == mysql
        assert pgsql_dialect.type(type_enum, size) == pgsql
        assert sqlite_dialect.type(type_enum, size) == sqlite


FLAGS = (
    "boolean",
    "distinct_on",
    "generated_by_default_as_identity",
    "index_exists",
    "lateral",
    "on_conflict",
    "returning",
    "savepoints",
    "table_exists",
)


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        (SQLDialect(Driver.SQLITE), (True, False, True, False, True, False, False, True, False)),
        (MySQLDialect(Driver.MYSQL, "8.0.13"), (False, False, False, False, False, True, False, True, True)),
        (MySQLDialect(Driver.MYSQL, "8.0.14"), (False, False, False, False, True, True, False, True, True)),
        (MySQLDialect(Driver.MARIADB, "10.4.0"), (False, False, False, False, False, True, False, True, True)),
        (MySQLDialect(Driver.MARIADB, "10.5.0"), (False, False, False, False, False, True, True, True, True)),
        (PgSQLDialect(Driver.PGSQL, "9.4.26"), (True, True, False, False, True, False, True, True, True)),
        (PgSQLDialect(Driver.PGSQL, "9.6.24"), (True, True, False, True, True, True, True, True, True)),
        (PgSQLDialect(Driver.PGSQL, "16.2"), (True, True, False, True, True, True, True, True, True)),
        (PgSQLDialect(Driver.PGSQL, "17.0"), (True, True, True, True, True, True, True, True, True)),
        (SQLiteDialect(Driver.SQLITE, "3.23.0"), (False, False, False, True, False, False, False, True, True)),
        (SQLiteDialect(Driver.SQLITE, "3.24.0"), (False, False, False, True, False, True, False, True, True)),
        (SQLiteDialect(Driver.SQLITE, "3.35.0"), (False, False, False, True, False, True, True, True, True)),
    ],
    ids=lambda value: f"{type(value).__name__}-{value.driver}-{value.version}" if isinstance(value, SQLDialect) else "",
)
def test_feature_flags(dialect: SQLDialect, expected: tuple[bool, ...]) -> None:
    assert tuple(getattr(dialect, flag) for flag in FLAGS) == expected


class TestTransactions:
    def test_sql(self, sqlite_dialect: SQLiteDialect) -> None:
        assert sqlite_dialect.begin_transaction().query == "BEGIN TRANSACTION"
        assert sqlite_dialect.commit_transaction().query == "COMMIT"
        assert sqlite_dialect.rollback_transaction().query == "ROLLBACK"
        assert sqlite_dialect.begin_savepoint("savepoint_1").query == 'SAVEPOINT "savepoint_1"'
        assert sqlite_dialect.commit_savepoint("savepoint_1").query == 'RELEASE SAVEPOINT "savepoint_1"'
        assert sqlite_dialect.rollback_savepoint("savepoint_1").query == 'ROLLBACK TO "savepoint_1"'

    def test_mysql_starts_transactions_with_start_transaction(self, mysql_dialect: MySQLDialect) -> None:
        assert mysql_dialect.begin_transaction().query == "START TRANSACTION"
        assert mysql_dialect.begin_savepoint("s").query == "SAVEPOINT `s`"
