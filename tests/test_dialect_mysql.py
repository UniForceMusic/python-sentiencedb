"""MySQL and MariaDB statement generation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest

from sentiencedb import Driver, QueryError
from sentiencedb.dialects import MySQLDialect
from sentiencedb.options import Options
from sentiencedb.queries.enums import ConditionEnum, JoinEnum, TypeEnum
from sentiencedb.queries.objects import (
    AlterColumn,
    Column,
    Condition,
    ConditionNode,
    CurrentTimestamp,
    DropConstraint,
    JoinClause,
    OnConflict,
    QueryWithParams,
    Raw,
    SubQuery,
    Type,
)
from sentiencedb.queries.query import Query
from sentiencedb.queries.type_aliases import Scalar

StaticQuery = Callable[..., Query]


def where(dialect: MySQLDialect, *conditions: ConditionNode) -> tuple[str, list[Scalar]]:
    query_with_params = dialect.select(table="t", where=conditions)

    return query_with_params.query.split(" WHERE ", 1)[1], list(query_with_params.params)


def mysql(version: str, options: Options | None = None) -> MySQLDialect:
    return MySQLDialect(Driver.MYSQL, version, options)


def mariadb(version: str) -> MySQLDialect:
    return MySQLDialect(Driver.MARIADB, version)


class TestEscaping:
    def test_identifiers_double_the_backtick(self, mysql_dialect: MySQLDialect) -> None:
        """PHP escaped a backtick inside an identifier as ``\\```, which MySQL rejects (bug A2)."""
        assert mysql_dialect.select(table="we`ird", columns=["a\\b"]).query == "SELECT `a\\b` FROM `we``ird`"

    def test_string_literals_use_single_quotes_and_backslashes(self, mysql_dialect: MySQLDialect) -> None:
        query_with_params = mysql_dialect.create_table(
            table="t", columns=[Column("a", Type(TypeEnum.VARCHAR), default='it\'s "x"\\')]
        )

        assert query_with_params.query == "CREATE TABLE `t` (`a` VARCHAR(255) DEFAULT 'it\\'s \"x\"\\\\')"
        assert QueryWithParams("SELECT ?", [True]).to_sql(mysql_dialect) == "SELECT 1"


class TestConditions:
    def test_like_binary_when_case_sensitive(self, mysql_dialect: MySQLDialect) -> None:
        assert where(mysql_dialect, Condition(ConditionEnum.LIKE, "c", "a%")) == ("`c` LIKE BINARY ?", ["a%"])
        assert where(mysql_dialect, Condition(ConditionEnum.NOT_LIKE, "c", "a%")) == ("`c` NOT LIKE BINARY ?", ["a%"])
        assert where(mysql_dialect, Condition(ConditionEnum.LIKE, "c", "a%", case_insensitive=True)) == (
            "`c` LIKE ?",
            ["a%"],
        )
        assert where(mysql_dialect, Condition(ConditionEnum.NOT_LIKE, "c", "a%", case_insensitive=True)) == (
            "`c` NOT LIKE ?",
            ["a%"],
        )

    def test_like_before_4_uses_the_generic_form(self) -> None:
        assert where(mysql("3.23.58"), Condition(ConditionEnum.LIKE, "c", "a%", case_insensitive=True)) == (
            "lower(`c`) LIKE lower(?)",
            ["a%"],
        )

    def test_glob_becomes_like(self, mysql_dialect: MySQLDialect) -> None:
        assert where(mysql_dialect, Condition(ConditionEnum.GLOB, "c", "a?*")) == ("`c` LIKE BINARY ?", ["a_%"])

    def test_regexp_like_on_mysql_8(self, mysql_dialect: MySQLDialect) -> None:
        assert where(mysql_dialect, Condition(ConditionEnum.NOT_REGEX, "c", "^a", flags="i")) == (
            "NOT regexp_like(`c`, ?, ?)",
            ["^a", "i"],
        )

    @pytest.mark.parametrize(
        "dialect", [mysql("5.7.44"), mysql("8.4.3", {"use_regexp": True}), mariadb("11.4.4-MariaDB")]
    )
    def test_regexp_operator(self, dialect: MySQLDialect) -> None:
        assert where(dialect, Condition(ConditionEnum.REGEX, "c", "^a", flags="i")) == ("`c` REGEXP ?", ["(?i)^a"])
        assert where(dialect, Condition(ConditionEnum.NOT_REGEX, "c", "^a")) == ("`c` NOT REGEXP ?", ["^a"])

    @pytest.mark.parametrize(
        ("value", "cast_type"),
        [(True, "UNSIGNED"), (1, "SIGNED"), (1.5, "DECIMAL"), ("x", "CHAR"), (datetime(2024, 1, 2), "DATETIME(6)")],
    )
    def test_cast_types(self, mysql_dialect: MySQLDialect, value: Scalar, cast_type: str) -> None:
        assert where(mysql_dialect, Condition(ConditionEnum.EQUALS, "c", value, cast=True)) == (
            f"cast(`c` AS {cast_type}) = cast(? AS {cast_type})",
            [value],
        )


class TestSelect:
    def test_lateral_from_mysql_8_0_14(self, mysql_dialect: MySQLDialect, static_query: StaticQuery) -> None:
        joins = [JoinClause(JoinEnum.LEFT_JOIN_LATERAL, SubQuery(static_query("SELECT 1"), "x"))]

        assert mysql_dialect.select(table="t", joins=joins).query == (
            "SELECT * FROM `t` LEFT JOIN LATERAL (SELECT 1) AS `x` ON 1"
        )

        for dialect in (mysql("8.0.13"), mariadb("11.4.4")):
            with pytest.raises(QueryError, match="LATERAL"):
                dialect.select(table="t", joins=joins)

    def test_no_distinct_on(self, mariadb_dialect: MySQLDialect) -> None:
        assert mariadb_dialect.select(table="t", distinct=[]).query == "SELECT DISTINCT * FROM `t`"

        with pytest.raises(QueryError, match="DISTINCT ON"):
            mariadb_dialect.select(table="t", distinct=["a"])


class TestInsert:
    def test_do_nothing_is_insert_ignore(self, mysql_dialect: MySQLDialect) -> None:
        query_with_params = mysql_dialect.insert(table="t", values=[{"a": 1}, {"a": 2}], on_conflict=OnConflict(["a"]))

        assert query_with_params.query == "INSERT IGNORE INTO `t` (`a`) VALUES (?), (?)"
        assert list(query_with_params.params) == [1, 2]

    def test_do_nothing_with_last_insert_id(self, mysql_dialect: MySQLDialect) -> None:
        query_with_params = mysql_dialect.insert(
            table="t", values=[{"a": 1}], on_conflict=OnConflict("uq_a"), last_insert_id="id"
        )

        assert query_with_params.query == (
            "INSERT INTO `t` (`a`) VALUES (?) ON DUPLICATE KEY UPDATE `id` = last_insert_id(`id`)"
        )

    def test_update_every_inserted_column(self, mysql_dialect: MySQLDialect) -> None:
        query_with_params = mysql_dialect.insert(
            table="users",
            values=[{"email": "a", "name": "b"}],
            on_conflict=OnConflict(["email"], {}),
            last_insert_id="id",
        )

        assert query_with_params.query == (
            "INSERT INTO `users` (`email`, `name`) VALUES (?, ?) ON DUPLICATE KEY UPDATE"
            " `email` = values(`email`), `name` = values(`name`), `id` = last_insert_id(`id`)"
        )

    def test_explicit_updates(self, mysql_dialect: MySQLDialect) -> None:
        query_with_params = mysql_dialect.insert(
            table="t", values=[{"a": 1}], on_conflict=OnConflict(["a"], {"n": Raw("n + 1"), "at": "x"})
        )

        assert (
            query_with_params.query == "INSERT INTO `t` (`a`) VALUES (?) ON DUPLICATE KEY UPDATE `n` = n + 1, `at` = ?"
        )
        assert list(query_with_params.params) == [1, "x"]

    def test_returning_only_on_mariadb_10_5(self, mysql_dialect: MySQLDialect, mariadb_dialect: MySQLDialect) -> None:
        assert mysql_dialect.insert(table="t", values=[{"a": 1}], returning=[]).query == (
            "INSERT INTO `t` (`a`) VALUES (?)"
        )
        assert mariadb_dialect.insert(table="t", values=[{"a": 1}], returning=["id"]).query == (
            "INSERT INTO `t` (`a`) VALUES (?) RETURNING `id`"
        )
        assert mariadb_dialect.delete(table="t", returning=[]).query == "DELETE FROM `t` RETURNING *"
        assert mariadb("10.4.0").delete(table="t", returning=[]).query == "DELETE FROM `t`"

    def test_update_never_returns(self, mariadb_dialect: MySQLDialect) -> None:
        assert (mariadb_dialect.returning, mariadb_dialect.update_returning) == (True, False)
        assert mariadb_dialect.update(table="t", updates={"a": 1}, returning=[]).query == "UPDATE `t` SET `a` = ?"


class TestCreateTable:
    def test_auto_increment_joins_the_primary_key(self, mysql_dialect: MySQLDialect) -> None:
        query_with_params = mysql_dialect.create_table(
            table="t",
            columns=[
                Column("id", Type(TypeEnum.INTEGER, 64), not_null=True, generated_by_default_as_identity=True),
                Column("flag", Type(TypeEnum.BOOLEAN), default=True),
            ],
            if_not_exists=True,
        )

        assert query_with_params.query == (
            "CREATE TABLE IF NOT EXISTS `t` (`id` BIGINT NOT NULL AUTO_INCREMENT, `flag` TINYINT DEFAULT 1,"
            " PRIMARY KEY (`id`))"
        )

    def test_auto_increment_already_in_the_primary_key(self, mysql_dialect: MySQLDialect) -> None:
        query_with_params = mysql_dialect.create_table(
            table="t",
            columns=[Column("id", "INT", generated_by_default_as_identity=True), Column("k", "INT")],
            primary_keys=["k", "id"],
        )

        assert query_with_params.query == (
            "CREATE TABLE `t` (`id` INT AUTO_INCREMENT, `k` INT, PRIMARY KEY (`k`, `id`))"
        )

    def test_current_timestamp_gets_the_datetime_precision(self, mysql_dialect: MySQLDialect) -> None:
        query_with_params = mysql_dialect.create_table(
            table="t",
            columns=[
                Column("a", Type(TypeEnum.DATETIME, 6), default=CurrentTimestamp()),
                Column("b", Type(TypeEnum.DATETIME), default=CurrentTimestamp()),
                Column("c", "DATETIME(3)", default=CurrentTimestamp(3)),
            ],
        )

        assert query_with_params.query == (
            "CREATE TABLE `t` (`a` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), `b` DATETIME DEFAULT CURRENT_TIMESTAMP,"
            " `c` DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3))"
        )

    def test_if_not_exists_needs_3_23(self) -> None:
        assert mysql("3.22.32").create_table(table="t", columns=[Column("a", "INT")], if_not_exists=True).query == (
            "CREATE TABLE `t` (`a` INT)"
        )


class TestAlterAndIndexes:
    def test_modify_and_drop_index(self, mysql_dialect: MySQLDialect) -> None:
        statements = mysql_dialect.alter_table(
            table="t", alters=[AlterColumn("a", "BIGINT NOT NULL"), DropConstraint("uq")]
        )

        assert [statement.query for statement in statements] == [
            "ALTER TABLE `t` MODIFY `a` BIGINT NOT NULL",
            "ALTER TABLE `t` DROP INDEX `uq`",
        ]

    def test_indexes(self, mysql_dialect: MySQLDialect) -> None:
        assert mysql_dialect.create_index(table="t", name="i", columns=["a"], if_not_exists=True).query == (
            "CREATE INDEX `i` ON `t` (`a`)"
        )
        assert mysql_dialect.drop_index(table=["db", "t"], name="i", if_exists=True).query == (
            "DROP INDEX `i` ON `db`.`t`"
        )
        assert mysql_dialect.drop_table(table="t", if_exists=True).query == "DROP TABLE IF EXISTS `t`"

    @pytest.mark.parametrize("driver", [Driver.MYSQL, Driver.MARIADB])
    def test_partial_indexes_are_unsupported(self, driver: Driver) -> None:
        dialect = MySQLDialect(driver, "11.4.4")

        with pytest.raises(QueryError, match="partial indexes"):
            dialect.create_index(table="t", name="i", columns=["a"], where=[Condition(ConditionEnum.EQUALS, "a", 1)])
