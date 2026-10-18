from __future__ import annotations

from datetime import datetime

import pytest

from sentiencedb.dialects import MySQLDialect, PgSQLDialect, SQLiteDialect
from sentiencedb.exceptions import QueryWithParamsError
from sentiencedb.queries.objects import QueryWithParams


class TestNamedParamsToQuestionMarks:
    def test_positional_query_is_returned_unchanged(self, sqlite_dialect: SQLiteDialect) -> None:
        query_with_params = QueryWithParams("SELECT ? AS a", [1])

        assert query_with_params.named_params_to_question_marks(sqlite_dialect) is query_with_params

    def test_rewrites_named_params_in_placeholder_order(self, sqlite_dialect: SQLiteDialect) -> None:
        converted = QueryWithParams("SELECT :b, :a, :b", {"a": 1, "b": 2}).named_params_to_question_marks(
            sqlite_dialect
        )

        assert converted == QueryWithParams("SELECT ?, ?, ?", [2, 1, 2])

    def test_accepts_keys_with_colon(self, sqlite_dialect: SQLiteDialect) -> None:
        converted = QueryWithParams("a = :a", {":a": 1}).named_params_to_question_marks(sqlite_dialect)

        assert converted == QueryWithParams("a = ?", [1])

    def test_skips_casts_strings_identifiers_and_comments(self, sqlite_dialect: SQLiteDialect) -> None:
        query = """SELECT x::text, ':no', ":no", `:no`, [:no], a -- :no
            /* :no */ FROM t WHERE b = :yes"""

        converted = QueryWithParams(query, {"yes": 1}).named_params_to_question_marks(sqlite_dialect)

        assert converted.query == query.replace(":yes", "?")
        assert converted.params == [1]

    def test_missing_named_param_raises(self, sqlite_dialect: SQLiteDialect) -> None:
        with pytest.raises(QueryWithParamsError, match="named param :missing does not exist"):
            QueryWithParams("a = :missing", {"a": 1}).named_params_to_question_marks(sqlite_dialect)

    def test_mixing_placeholder_styles_raises(self, sqlite_dialect: SQLiteDialect) -> None:
        with pytest.raises(QueryWithParamsError, match="cannot mix"):
            QueryWithParams("a = :a AND b = ?", {"a": 1}).named_params_to_question_marks(sqlite_dialect)


class TestQuestionMarksToPercentS:
    def test_converts_placeholders_and_doubles_literal_percent_signs(self, pgsql_dialect: PgSQLDialect) -> None:
        converted = QueryWithParams(
            "SELECT '50% ?', 10 % 3 FROM t WHERE a LIKE ? AND b = ?", ["x%", 2]
        ).question_marks_to_percent_s(pgsql_dialect)

        assert converted == QueryWithParams("SELECT '50%% ?', 10 %% 3 FROM t WHERE a LIKE %s AND b = %s", ["x%", 2])

    def test_converts_named_params_first(self, pgsql_dialect: PgSQLDialect) -> None:
        converted = QueryWithParams("a = :a OR b = :a", {"a": 1}).question_marks_to_percent_s(pgsql_dialect)

        assert converted == QueryWithParams("a = %s OR b = %s", [1, 1])

    def test_count_mismatch_raises(self, pgsql_dialect: PgSQLDialect) -> None:
        with pytest.raises(QueryWithParamsError, match="question mark and param count do not match"):
            QueryWithParams("a = ? AND b = ?", [1]).question_marks_to_percent_s(pgsql_dialect)

    def test_percent_signs_in_dollar_quotes_and_comments_are_doubled(self, pgsql_dialect: PgSQLDialect) -> None:
        converted = QueryWithParams("SELECT $$5% ?$$ /* 5% ? */, ?", [1]).question_marks_to_percent_s(pgsql_dialect)

        assert converted.query == "SELECT $$5%% ?$$ /* 5%% ? */, %s"


# Each query has exactly one real placeholder, the last ``?``; every other ``?`` is hidden from the tokenizer.
HIDDEN_PLACEHOLDERS = {
    "sqlite": [
        "SELECT 'C:\\' AS a, 'ok?' AS b, ?",
        "SELECT 'it''s ?', ?",
        'SELECT "a?", `b?`, [c?], ?',
        "SELECT 1 -- ?\n, ?",
        "SELECT /* ? */ ?",
    ],
    "pgsql": [
        "SELECT 'C:\\' AS a, 'ok?' AS b, ?",
        "SELECT E'it\\'s ?', e'\\\\', ?",
        "SELECT $$what?$$, $body$ $$ ? $$ $body$, ?",
        "SELECT /* outer /* ? */ still ? */ ?",
        'SELECT "a?", ?',
        "SELECT a$b$c, x$1, ?",  # identifiers with $, not dollar quotes
    ],
    "mysql": [
        "SELECT 'it\\'s ?', \"also \\\" ?\", ?",
        "SELECT 1 # what?\n, ?",
        "SELECT 1 -- what?\n, ?",
        "SELECT `a?`, /* ? */ ?",
    ],
}


@pytest.mark.parametrize(
    ("engine", "query"), [(engine, query) for engine, queries in HIDDEN_PLACEHOLDERS.items() for query in queries]
)
def test_tokenizer_follows_each_engines_lexical_rules(engine: str, query: str, request: pytest.FixtureRequest) -> None:
    dialect = request.getfixturevalue(f"{engine}_dialect")

    assert QueryWithParams(query, [1]).count_question_marks(dialect) == 1
    assert QueryWithParams(query, [1]).to_sql(dialect) == query[: query.rindex("?")] + "1"


@pytest.mark.parametrize(
    ("engine", "query", "count"),
    [
        ("sqlite", "SELECT 'C:\\', '?'", 0),  # no backslash escapes in standard strings
        ("pgsql", "SELECT 'C:\\', '?'", 0),
        ("mysql", "SELECT 'C:\\', '?'", 1),  # \' escapes the quote: the string is 'C:\', ' and ? is outside it
        ("mysql", "SELECT 1--?", 1),  # 1 - (-?): MySQL needs whitespace after --
        ("pgsql", "SELECT 1 # ?", 1),  # # is an operator on PostgreSQL
        ("sqlite", "SELECT /* /* */ ?", 1),  # block comments only nest on PostgreSQL
        ("pgsql", "SELECT /* /* */ ? */", 0),
        ("mariadb", "SELECT /* /* */ ?", 1),
    ],
)
def test_tokenizer_differences_between_engines(
    engine: str, query: str, count: int, request: pytest.FixtureRequest
) -> None:
    dialect = request.getfixturevalue(f"{engine}_dialect")

    assert QueryWithParams(query).count_question_marks(dialect) == count


class TestToSql:
    def test_without_params_returns_query_verbatim(self, sqlite_dialect: SQLiteDialect) -> None:
        assert QueryWithParams("SELECT '?'").to_sql(sqlite_dialect) == "SELECT '?'"

    def test_interpolates_positional_params(self, sqlite_dialect: SQLiteDialect) -> None:
        query_with_params = QueryWithParams(
            "INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)", [None, True, 3, 1.5, "it's", datetime(2024, 1, 2, 3, 4, 5)]
        )

        assert query_with_params.to_sql(sqlite_dialect) == (
            "INSERT INTO t VALUES (NULL, 1, 3, 1.5, 'it''s', '2024-01-02 03:04:05')"
        )

    def test_interpolates_named_params(self, pgsql_dialect: PgSQLDialect) -> None:
        query_with_params = QueryWithParams("SELECT :flag, :flag::int", {"flag": False})

        assert query_with_params.to_sql(pgsql_dialect) == "SELECT FALSE, FALSE::int"

    def test_uses_the_dialect_string_escaping(self, mysql_dialect: MySQLDialect) -> None:
        assert QueryWithParams("SELECT ?", ["a'b\\c"]).to_sql(mysql_dialect) == "SELECT 'a''b\\\\c'"

    def test_too_few_params_raises(self, sqlite_dialect: SQLiteDialect) -> None:
        with pytest.raises(QueryWithParamsError):
            QueryWithParams("SELECT ?, ?", [1]).to_sql(sqlite_dialect)

    def test_too_many_params_raises(self, sqlite_dialect: SQLiteDialect) -> None:
        with pytest.raises(QueryWithParamsError):
            QueryWithParams("SELECT ?", [1, 2]).to_sql(sqlite_dialect)

    def test_is_immutable(self) -> None:
        query_with_params = QueryWithParams("SELECT 1")

        with pytest.raises(AttributeError):
            query_with_params.query = "SELECT 2"  # type: ignore[misc]
