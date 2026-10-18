from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from unittest.mock import create_autospec

import pytest

from sentiencedb import (
    alias,
    current_timestamp,
    escape_ansi,
    escape_backslash,
    expression,
    expressionf,
    identifier,
    raw,
    sub_query,
)
from sentiencedb.database_abc import DatabaseABC
from sentiencedb.dialects import MySQLDialect, PgSQLDialect, SQLiteDialect
from sentiencedb.exceptions import QueryError
from sentiencedb.queries.enums import ConditionEnum
from sentiencedb.queries.objects import Alias, CurrentTimestamp, Expression, ExpressionF, Identifier, Raw, SubQuery
from sentiencedb.queries.objects.clauses import Condition
from sentiencedb.queries.objects.query_with_params import QueryWithParams
from sentiencedb.queries.query import Query


def test_raw_is_verbatim(sqlite_dialect: SQLiteDialect) -> None:
    sql = raw("count(*)")

    assert sql == Raw("count(*)")
    assert sql.sql(sqlite_dialect) == sql.raw_sql(sqlite_dialect) == "count(*)"
    assert sql.params(sqlite_dialect) == []


def test_identifier_is_escaped_per_dialect(sqlite_dialect: SQLiteDialect, mysql_dialect: MySQLDialect) -> None:
    sql = identifier(["users", "id"])

    assert sql == Identifier(["users", "id"])
    assert sql.sql(sqlite_dialect) == '"users"."id"'
    assert sql.raw_sql(mysql_dialect) == "`users`.`id`"
    assert sql.params(sqlite_dialect) == []


def test_identifier_path_may_contain_sql(sqlite_dialect: SQLiteDialect) -> None:
    assert identifier([raw("excluded"), "name"]).sql(sqlite_dialect) == 'excluded."name"'


def test_expression_binds_params_and_inlines_them_in_raw_sql(sqlite_dialect: SQLiteDialect) -> None:
    sql = expression("coalesce(a, ?) + ?", [0, 1.5])

    assert sql == Expression("coalesce(a, ?) + ?", [0, 1.5])
    assert sql.sql(sqlite_dialect) == "coalesce(a, ?) + ?"
    assert sql.params(sqlite_dialect) == [0, 1.5]
    assert sql.raw_sql(sqlite_dialect) == "coalesce(a, 0) + 1.5"


@pytest.mark.parametrize(
    ("precision", "expected"),
    [(None, "CURRENT_TIMESTAMP"), (0, "CURRENT_TIMESTAMP"), (3, "CURRENT_TIMESTAMP(3)"), (-6, "CURRENT_TIMESTAMP(6)")],
)
def test_current_timestamp(sqlite_dialect: SQLiteDialect, precision: int | None, expected: str) -> None:
    sql = current_timestamp(precision)

    assert sql == CurrentTimestamp(precision)
    assert sql.sql(sqlite_dialect) == sql.raw_sql(sqlite_dialect) == expected
    assert sql.params(sqlite_dialect) == []


def test_alias_and_sub_query_factories(static_query: Callable[..., Query]) -> None:
    query = static_query("SELECT 1")

    assert alias(["u", "name"], "user_name") == Alias(["u", "name"], "user_name")
    assert sub_query(query, "sq") == SubQuery(query, "sq")


class TestExpressionF:
    def test_binds_values_cast_by_modifier(self, pgsql_dialect: PgSQLDialect) -> None:
        sql = expressionf("%b AND %d = %.2f OR %s", 1, "42", "3.14159", 7)

        assert sql == ExpressionF("%b AND %d = %.2f OR %s", (1, "42", "3.14159", 7))
        assert sql.sql(pgsql_dialect) == "? AND ? = ? OR ?"
        assert sql.params(pgsql_dialect) == [True, 42, 3.14, "7"]
        assert sql.raw_sql(pgsql_dialect) == "TRUE AND 42 = 3.14 OR '7'"

    def test_none_and_datetimes_are_bound_unchanged(self, sqlite_dialect: SQLiteDialect) -> None:
        moment = datetime(2024, 1, 2, 3, 4, 5)
        sql = expressionf("a IS %s AND b = %s", None, moment)

        assert sql.params(sqlite_dialect) == [None, moment]
        assert sql.raw_sql(sqlite_dialect) == "a IS NULL AND b = '2024-01-02 03:04:05'"

    def test_inlines_sql_and_queries(self, sqlite_dialect: SQLiteDialect, static_query: Callable[..., Query]) -> None:
        sql = expressionf("%s IN (%s) AND %s", raw('"a"'), static_query("SELECT b FROM t WHERE c = ?", [1]), 2)

        assert sql.sql(sqlite_dialect) == '"a" IN (SELECT b FROM t WHERE c = ?) AND ?'
        assert sql.params(sqlite_dialect) == [1, "2"]
        assert sql.raw_sql(sqlite_dialect) == "\"a\" IN (SELECT b FROM t WHERE c = 1) AND '2'"

    def test_a_dialect_renders_it_and_its_sub_select_once(self, sqlite_dialect: SQLiteDialect) -> None:
        compiled: list[str] = []

        class CountingQuery(Query):
            def to_query_with_params(self) -> QueryWithParams:
                compiled.append("inner")

                return QueryWithParams("SELECT b FROM t WHERE c = :c", {"c": 1})

        inner = CountingQuery(create_autospec(DatabaseABC, instance=True), sqlite_dialect)
        query_with_params = sqlite_dialect.select(
            table="u", where=[Condition(ConditionEnum.RAW, None, expressionf("a IN (%s)", inner))]
        )

        assert query_with_params == QueryWithParams('SELECT * FROM "u" WHERE (a IN (SELECT b FROM t WHERE c = ?))', [1])
        assert compiled == ["inner"]

    def test_double_percent_is_a_literal_percent(self, sqlite_dialect: SQLiteDialect) -> None:
        sql = expressionf("a LIKE '10%%' AND b = %d%%%d", 1, 2)

        assert sql.sql(sqlite_dialect) == "a LIKE '10%' AND b = ?%?"
        assert sql.params(sqlite_dialect) == [1, 2]

    def test_without_modifiers_is_verbatim(self, sqlite_dialect: SQLiteDialect) -> None:
        assert expressionf("count(*)").sql(sqlite_dialect) == "count(*)"

    def test_too_few_values_raises(self, sqlite_dialect: SQLiteDialect) -> None:
        with pytest.raises(QueryError, match="2 arguments are required, 1 given"):
            expressionf("%d, %d", 1).sql(sqlite_dialect)


def test_escape_ansi_doubles_each_char() -> None:
    assert escape_ansi("it's 100%", ["'", "%", "'"]) == "it''s 100%%"


def test_escape_backslash_escapes_backslash_and_each_char() -> None:
    assert escape_backslash("50%_a\\b", ["%", "_", "\\"]) == "50\\%\\_a\\\\b"
