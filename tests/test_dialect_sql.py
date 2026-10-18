"""Statement generation of the generic :class:`SQLDialect`, shared by every concrete dialect."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from sentiencedb import QueryError
from sentiencedb.dialects import SQLDialect
from sentiencedb.queries.enums import (
    ChainEnum,
    ConditionEnum,
    JoinEnum,
    OrderByDirectionEnum,
    ReferentialActionEnum,
    TypeEnum,
    UnionEnum,
)
from sentiencedb.queries.objects import (
    AddColumn,
    AddForeignKeyConstraint,
    AddPrimaryKeys,
    AddUniqueConstraint,
    Alias,
    AlterColumn,
    Column,
    Condition,
    ConditionGroup,
    ConditionNode,
    CurrentTimestamp,
    DropColumn,
    DropConstraint,
    Expression,
    ExpressionF,
    ForeignKeyConstraint,
    Identifier,
    JoinClause,
    OnConflict,
    OrderBy,
    QueryWithParams,
    Raw,
    RenameColumn,
    SubQuery,
    Type,
    UnionClause,
    UniqueConstraint,
)
from sentiencedb.queries.query import Query
from sentiencedb.queries.type_aliases import Scalar

StaticQuery = Callable[..., Query]


def where(dialect: SQLDialect, *conditions: ConditionNode) -> tuple[str, list[Scalar]]:
    """The ``WHERE`` clause body and params of ``SELECT * FROM t WHERE <conditions>``."""
    query_with_params = dialect.select(table="t", where=conditions)

    return query_with_params.query.split(" WHERE ", 1)[1], list(query_with_params.params)


class TestSelect:
    def test_minimal(self, sql_dialect: SQLDialect) -> None:
        query_with_params = sql_dialect.select(table="users")

        assert query_with_params.query == 'SELECT * FROM "users"'
        assert list(query_with_params.params) == []

    def test_every_clause(self, sql_dialect: SQLDialect) -> None:
        query_with_params = sql_dialect.select(
            table=Alias(["main", "users"], "u"),
            columns=["id", Alias(["u", "name"], "n"), Raw("count(*)")],
            distinct=[],
            joins=[JoinClause(JoinEnum.LEFT_JOIN, "posts", [Condition(ConditionEnum.EQUALS, "a", 1)])],
            where=[Condition(ConditionEnum.EQUALS, "b", 2)],
            group_by=["id", ["u", "name"]],
            having=[Condition(ConditionEnum.GREATER_THAN, Raw("count(*)"), 3)],
            order_by=[OrderBy("id", OrderByDirectionEnum.DESC), OrderBy(Raw("n"))],
            limit=10,
            offset=5,
        )

        assert query_with_params.query == (
            'SELECT DISTINCT "id", "u"."name" AS "n", count(*) FROM "main"."users" AS "u"'
            ' LEFT JOIN "posts" ON "a" = ? WHERE "b" = ? GROUP BY "id", "u"."name" HAVING count(*) > ?'
            ' ORDER BY "id" DESC, n ASC LIMIT 10 OFFSET 5'
        )
        assert list(query_with_params.params) == [1, 2, 3]

    def test_offset_requires_limit(self, sql_dialect: SQLDialect) -> None:
        assert sql_dialect.select(table="t", offset=5).query == 'SELECT * FROM "t"'
        assert sql_dialect.select(table="t", limit=0).query == 'SELECT * FROM "t" LIMIT 0'

    def test_distinct_on_is_unsupported(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="DISTINCT ON"):
            sql_dialect.select(table="t", distinct=["a"])

    def test_sub_queries_as_columns_and_table(self, sql_dialect: SQLDialect, static_query: StaticQuery) -> None:
        query_with_params = sql_dialect.select(
            table=SubQuery(static_query("SELECT * FROM a WHERE x = ?", [3]), "s"),
            columns=[static_query("SELECT ?", [1]), SubQuery(static_query("SELECT ?", [2]), "two")],
            where=[Condition(ConditionEnum.EQUALS, "y", 4)],
        )

        assert query_with_params.query == (
            'SELECT (SELECT ?), (SELECT ?) AS "two" FROM (SELECT * FROM a WHERE x = ?) AS "s" WHERE "y" = ?'
        )
        assert list(query_with_params.params) == [1, 2, 3, 4]

    def test_sub_query_with_named_params_is_made_positional(self, sql_dialect: SQLDialect, mock_database: Any) -> None:
        class NamedQuery(Query):
            def to_query_with_params(self) -> QueryWithParams:
                return QueryWithParams("SELECT :a, :b", {"b": 2, "a": 1})

        named = NamedQuery(mock_database, sql_dialect)
        query_with_params = sql_dialect.select(table="t", columns=[named])

        assert query_with_params.query == 'SELECT (SELECT ?, ?) FROM "t"'
        assert list(query_with_params.params) == [1, 2]

    def test_unions(self, sql_dialect: SQLDialect, static_query: StaticQuery) -> None:
        query_with_params = sql_dialect.select(
            table="a",
            where=[Condition(ConditionEnum.EQUALS, "x", 1)],
            unions=[
                UnionClause(UnionEnum.UNION, static_query("SELECT * FROM b WHERE y = ?", [2])),
                UnionClause(UnionEnum.UNION_ALL, static_query("SELECT * FROM c")),
            ],
        )

        assert query_with_params.query == (
            '(SELECT * FROM "a" WHERE "x" = ?) UNION (SELECT * FROM b WHERE y = ?) UNION ALL (SELECT * FROM c)'
        )
        assert list(query_with_params.params) == [1, 2]


class TestJoins:
    def test_join_kinds(self, sql_dialect: SQLDialect) -> None:
        on = [Condition(ConditionEnum.EQUALS, ["p", "user_id"], Identifier(["u", "id"]))]
        query_with_params = sql_dialect.select(
            table="u",
            joins=[
                JoinClause(JoinEnum.LEFT_JOIN, Alias("posts", "p"), on),
                JoinClause(JoinEnum.INNER_JOIN, "c"),
                JoinClause(JoinEnum.CROSS_JOIN, "d"),
            ],
        )

        assert query_with_params.query == (
            'SELECT * FROM "u" LEFT JOIN "posts" AS "p" ON "p"."user_id" = "u"."id"'
            ' INNER JOIN "c" ON TRUE CROSS JOIN "d"'
        )

    def test_lateral_joins(self, sql_dialect: SQLDialect, static_query: StaticQuery) -> None:
        query_with_params = sql_dialect.select(
            table="u",
            joins=[
                JoinClause(JoinEnum.LEFT_JOIN_LATERAL, SubQuery(static_query("SELECT ?", [1]), "a")),
                JoinClause(JoinEnum.INNER_JOIN_LATERAL, SubQuery(static_query("SELECT ?", [2]), "b")),
                JoinClause(JoinEnum.CROSS_JOIN_LATERAL, SubQuery(static_query("SELECT ?", [3]), "c")),
            ],
        )

        assert query_with_params.query == (
            'SELECT * FROM "u" LEFT JOIN LATERAL (SELECT ?) AS "a" ON TRUE'
            ' INNER JOIN LATERAL (SELECT ?) AS "b" ON TRUE CROSS JOIN LATERAL (SELECT ?) AS "c"'
        )
        assert list(query_with_params.params) == [1, 2, 3]

    def test_raw_joins_keep_params_in_order(self, sql_dialect: SQLDialect) -> None:
        query_with_params = sql_dialect.select(
            table="u",
            joins=[Raw("NATURAL JOIN x"), ExpressionF("JOIN y ON y.a = %d", (5,))],
            where=[Condition(ConditionEnum.EQUALS, "b", 6)],
        )

        assert query_with_params.query == 'SELECT * FROM "u" NATURAL JOIN x JOIN y ON y.a = ? WHERE "b" = ?'
        assert list(query_with_params.params) == [5, 6]


class TestConditions:
    def test_equals_and_null(self, sql_dialect: SQLDialect) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.EQUALS, "a", "x")) == ('"a" = ?', ["x"])
        assert where(sql_dialect, Condition(ConditionEnum.NOT_EQUALS, "a", 1)) == ('"a" <> ?', [1])
        assert where(sql_dialect, Condition(ConditionEnum.EQUALS, "a", None)) == ('"a" IS NULL', [])
        assert where(sql_dialect, Condition(ConditionEnum.NOT_EQUALS, "a", None)) == ('"a" IS NOT NULL', [])

    @pytest.mark.parametrize(
        ("value", "cast_type"),
        [
            (True, "BOOLEAN"),
            (1, "BIGINT"),
            (1.5, "DECIMAL(30, 15)"),
            ("x", "TEXT"),
            (datetime(2024, 1, 2), "DATETIME"),
        ],
    )
    def test_equals_with_cast(self, sql_dialect: SQLDialect, value: Scalar, cast_type: str) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.EQUALS, "a", value, cast=True)) == (
            f'cast("a" AS {cast_type}) = cast(? AS {cast_type})',
            [value],
        )

    @pytest.mark.parametrize("value", [Decimal("1.5"), date(2024, 1, 2), b"x"])
    def test_equals_with_cast_of_other_scalars_is_uncast(self, sql_dialect: SQLDialect, value: Scalar) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.NOT_EQUALS, "a", value, cast=True)) == ('"a" <> ?', [value])

    def test_equals_with_cast_of_sql_and_sub_query_is_uncast(
        self, sql_dialect: SQLDialect, static_query: StaticQuery
    ) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.EQUALS, "a", Raw("b"), cast=True)) == ('"a" = b', [])
        assert where(sql_dialect, Condition(ConditionEnum.EQUALS, "a", static_query("SELECT ?", [1]), cast=True)) == (
            '"a" = (SELECT ?)',
            [1],
        )

    @pytest.mark.parametrize(
        "kind",
        [
            ConditionEnum.LESS_THAN,
            ConditionEnum.LESS_THAN_OR_EQUALS,
            ConditionEnum.GREATER_THAN,
            ConditionEnum.GREATER_THAN_OR_EQUALS,
        ],
    )
    def test_comparisons(self, sql_dialect: SQLDialect, kind: ConditionEnum) -> None:
        assert where(sql_dialect, Condition(kind, "a", 1)) == (f'"a" {kind} ?', [1])

    def test_between(self, sql_dialect: SQLDialect, static_query: StaticQuery) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.BETWEEN, "a", (1, 2))) == ('"a" BETWEEN ? AND ?', [1, 2])
        assert where(
            sql_dialect, Condition(ConditionEnum.NOT_BETWEEN, "a", (static_query("SELECT ?", [1]), Raw("now()")))
        ) == ('"a" NOT BETWEEN (SELECT ?) AND now()', [1])

    def test_between_requires_a_pair(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="pair"):
            where(sql_dialect, Condition(ConditionEnum.BETWEEN, "a", (1, 2, 3)))

    def test_like(self, sql_dialect: SQLDialect) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.LIKE, "a", "x%")) == ('"a" LIKE ?', ["x%"])
        assert where(sql_dialect, Condition(ConditionEnum.NOT_LIKE, "a", "x%")) == ('"a" NOT LIKE ?', ["x%"])
        assert where(sql_dialect, Condition(ConditionEnum.LIKE, "a", "x%", case_insensitive=True)) == (
            'lower("a") LIKE lower(?)',
            ["x%"],
        )

    def test_glob_becomes_like(self, sql_dialect: SQLDialect) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.GLOB, "a", "a*b?c%d_e\\f")) == (
            '"a" LIKE ?',
            ["a%b_c\\%d\\_e\\\\f"],
        )
        assert where(sql_dialect, Condition(ConditionEnum.NOT_GLOB, "a", "*", case_insensitive=True)) == (
            'lower("a") NOT LIKE lower(?)',
            ["%"],
        )

    def test_in(self, sql_dialect: SQLDialect, static_query: StaticQuery) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.IN, "a", [1, 2])) == ('"a" IN (?, ?)', [1, 2])
        assert where(sql_dialect, Condition(ConditionEnum.NOT_IN, "a", (Raw("b"), static_query("SELECT ?", [3])))) == (
            '"a" NOT IN (b, (SELECT ?))',
            [3],
        )
        assert where(
            sql_dialect, Condition(ConditionEnum.IN, "a", static_query("SELECT id FROM b WHERE c = ?", [4]))
        ) == (
            '"a" IN (SELECT id FROM b WHERE c = ?)',
            [4],
        )

    def test_empty_in(self, sql_dialect: SQLDialect) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.IN, "a", [])) == ("1 = 0", [])
        assert where(sql_dialect, Condition(ConditionEnum.NOT_IN, "a", ())) == ("1 = 1", [])

    def test_regex(self, sql_dialect: SQLDialect) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.REGEX, "a", "^x", flags="i")) == (
            'regexp_like("a", ?, ?)',
            ["^x", "i"],
        )
        assert where(sql_dialect, Condition(ConditionEnum.NOT_REGEX, "a", "^x")) == (
            'NOT regexp_like("a", ?, ?)',
            ["^x", ""],
        )

    def test_exists(self, sql_dialect: SQLDialect, static_query: StaticQuery) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.EXISTS, None, static_query("SELECT ?", [1]))) == (
            "EXISTS (SELECT ?)",
            [1],
        )
        assert where(sql_dialect, Condition(ConditionEnum.NOT_EXISTS, None, static_query("SELECT 1"))) == (
            "NOT EXISTS (SELECT 1)",
            [],
        )

    def test_raw(self, sql_dialect: SQLDialect) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.RAW, None, Expression("a = ? OR b = ?", [1, 2]))) == (
            "(a = ? OR b = ?)",
            [1, 2],
        )

    def test_custom_operators_are_verbatim(self, sql_dialect: SQLDialect) -> None:
        assert where(sql_dialect, Condition("@>", "a", "{1}")) == ('"a" @> ?', ["{1}"])
        assert where(sql_dialect, Condition("IN", "a", [1, 2])) == ('"a" IN (?, ?)', [1, 2])
        assert where(sql_dialect, Condition("=", "a", None)) == ('"a" = ?', [None])
        assert where(sql_dialect, Condition("LIKE", "a", "x", case_insensitive=True)) == ('"a" LIKE ?', ["x"])

    def test_bytes_value_is_one_param(self, sql_dialect: SQLDialect) -> None:
        assert where(sql_dialect, Condition(ConditionEnum.EQUALS, "a", b"\x00\x01")) == ('"a" = ?', [b"\x00\x01"])

    def test_column_is_required(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="requires a column"):
            where(sql_dialect, Condition(ConditionEnum.EQUALS, None, 1))

    def test_pattern_must_be_a_string(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="string pattern"):
            where(sql_dialect, Condition(ConditionEnum.GLOB, "a", 1))

    def test_chains_groups_and_negation(self, sql_dialect: SQLDialect) -> None:
        conditions = (
            Condition(ConditionEnum.EQUALS, "a", 1),
            Condition(ConditionEnum.EQUALS, "b", 2, chain=ChainEnum.OR),
            ConditionGroup(
                [
                    Condition(ConditionEnum.EQUALS, "c", 3),
                    ConditionGroup([Condition(ConditionEnum.EQUALS, "d", 4)], negate=True),
                    Condition(ConditionEnum.EQUALS, "e", 5, chain=ChainEnum.OR),
                ],
                negate=True,
            ),
            ConditionGroup([Condition(ConditionEnum.EQUALS, "f", 6)], chain=ChainEnum.OR),
        )

        assert where(sql_dialect, *conditions) == (
            '"a" = ? OR "b" = ? AND NOT ("c" = ? AND NOT ("d" = ?) OR "e" = ?) OR ("f" = ?)',
            [1, 2, 3, 4, 5, 6],
        )


class TestInsert:
    def test_rows_share_the_union_of_their_columns(self, sql_dialect: SQLDialect, static_query: StaticQuery) -> None:
        query_with_params = sql_dialect.insert(
            table=["main", "users"],
            values=[{"a": 1, "b": Raw("now()")}, {"c": static_query("SELECT ?", [3]), "a": 2}],
        )

        assert query_with_params.query == (
            'INSERT INTO "main"."users" ("a", "b", "c") VALUES (?, now(), DEFAULT), (?, DEFAULT, (SELECT ?))'
        )
        assert list(query_with_params.params) == [1, 2, 3]

    def test_unsupported_upsert_and_returning_are_ignored(self, sql_dialect: SQLDialect) -> None:
        query_with_params = sql_dialect.insert(
            table="t", values=[{"a": 1}], on_conflict=OnConflict(["a"], {}), returning=[], last_insert_id="id"
        )

        assert query_with_params.query == 'INSERT INTO "t" ("a") VALUES (?)'

    def test_requires_values(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="no insert values"):
            sql_dialect.insert(table="t", values=[])


class TestUpdateDelete:
    def test_update(self, sql_dialect: SQLDialect, static_query: StaticQuery) -> None:
        query_with_params = sql_dialect.update(
            table="t",
            updates={"a": 1, "b": Raw("b + 1"), "c": static_query("SELECT ?", [2])},
            where=[Condition(ConditionEnum.EQUALS, "id", 3)],
        )

        assert query_with_params.query == 'UPDATE "t" SET "a" = ?, "b" = b + 1, "c" = (SELECT ?) WHERE "id" = ?'
        assert list(query_with_params.params) == [1, 2, 3]

    def test_unsupported_returning_is_an_error(self, sql_dialect: SQLDialect) -> None:
        """Only ``INSERT`` drops it silently; :class:`InsertQuery` emulates it."""
        with pytest.raises(QueryError, match="RETURNING is not supported on UPDATE"):
            sql_dialect.update(table="t", updates={"a": 1}, returning=[])

        with pytest.raises(QueryError, match="RETURNING is not supported on DELETE"):
            sql_dialect.delete(table="t", returning=["a"])

    def test_update_requires_updates(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="no updates"):
            sql_dialect.update(table="t", updates={})

    def test_delete(self, sql_dialect: SQLDialect) -> None:
        assert sql_dialect.delete(table="t").query == 'DELETE FROM "t"'

        query_with_params = sql_dialect.delete(table="t", where=[Condition(ConditionEnum.IN, "id", [1, 2])])

        assert query_with_params.query == 'DELETE FROM "t" WHERE "id" IN (?, ?)'
        assert list(query_with_params.params) == [1, 2]


class TestCreateTable:
    def test_columns_keys_and_constraints(self, sql_dialect: SQLDialect) -> None:
        query_with_params = sql_dialect.create_table(
            table="t",
            columns=[
                Column("id", Type(TypeEnum.INTEGER, 64), not_null=True, generated_by_default_as_identity=True),
                Column("flag", Type(TypeEnum.BOOLEAN), default=True),
                Column("name", Type(TypeEnum.VARCHAR, 64), not_null=True, default="it's"),
                Column("ratio", Type(TypeEnum.FLOAT, 32), default=0.5),
                Column("at", Type(TypeEnum.DATETIME, 6), default=CurrentTimestamp()),
                Column("raw", "JSON", default=Raw("'{}'")),
            ],
            primary_keys=["id"],
            constraints=[
                UniqueConstraint(["name"]),
                UniqueConstraint(["name", "flag"], "uq_name_flag"),
                ForeignKeyConstraint(
                    ["id"], ["s", "other"], ["id"], "fk_other", ReferentialActionEnum.CASCADE, "SET NULL"
                ),
                ForeignKeyConstraint(["a", "b"], "x", ["c", "d"]),
                Raw("CHECK (ratio > 0)"),
            ],
            if_not_exists=True,
        )

        assert query_with_params.query == (
            'CREATE TABLE "t" ('
            '"id" BIGINT GENERATED BY DEFAULT AS IDENTITY NOT NULL, '
            '"flag" BOOLEAN DEFAULT TRUE, '
            "\"name\" VARCHAR(64) NOT NULL DEFAULT 'it''s', "
            '"ratio" DECIMAL(15, 7) DEFAULT 0.5, '
            '"at" DATETIME DEFAULT CURRENT_TIMESTAMP, '
            "\"raw\" JSON DEFAULT '{}', "
            'PRIMARY KEY ("id"), '
            'UNIQUE ("name"), '
            'CONSTRAINT "uq_name_flag" UNIQUE ("name", "flag"), '
            'CONSTRAINT "fk_other" FOREIGN KEY ("id") REFERENCES "s"."other" ("id")'
            " ON UPDATE CASCADE ON DELETE SET NULL, "
            'FOREIGN KEY ("a", "b") REFERENCES "x" ("c", "d"), '
            "CHECK (ratio > 0))"
        )
        assert list(query_with_params.params) == []

    def test_requires_columns(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="no table columns"):
            sql_dialect.create_table(table="t", columns=[])


class TestAlterTable:
    def test_one_statement_per_alter(self, sql_dialect: SQLDialect) -> None:
        statements = sql_dialect.alter_table(
            table=["s", "t"],
            alters=[
                AddColumn("a", Type(TypeEnum.TEXT), not_null=True, default=""),
                AlterColumn("a", "TYPE VARCHAR(10)"),
                RenameColumn("a", "b"),
                DropColumn("b"),
                AddPrimaryKeys(["id", "x"]),
                AddUniqueConstraint(["x"], "uq_x"),
                AddForeignKeyConstraint(["x"], "o", ["id"], on_delete=ReferentialActionEnum.RESTRICT),
                DropConstraint("uq_x"),
                Raw("OWNER TO bob"),
            ],
        )

        assert [statement.query for statement in statements] == [
            'ALTER TABLE "s"."t" ADD COLUMN "a" TEXT NOT NULL DEFAULT \'\'',
            'ALTER TABLE "s"."t" ALTER COLUMN "a" TYPE VARCHAR(10)',
            'ALTER TABLE "s"."t" RENAME COLUMN "a" TO "b"',
            'ALTER TABLE "s"."t" DROP COLUMN "b"',
            'ALTER TABLE "s"."t" ADD PRIMARY KEY ("id", "x")',
            'ALTER TABLE "s"."t" ADD CONSTRAINT "uq_x" UNIQUE ("x")',
            'ALTER TABLE "s"."t" ADD FOREIGN KEY ("x") REFERENCES "o" ("id") ON DELETE RESTRICT',
            'ALTER TABLE "s"."t" DROP CONSTRAINT "uq_x"',
            'ALTER TABLE "s"."t" OWNER TO bob',
        ]

    def test_requires_alters(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="no table alters"):
            sql_dialect.alter_table(table="t", alters=[])


class TestDropTableAndIndexes:
    def test_if_exists_needs_dialect_support(self, sql_dialect: SQLDialect) -> None:
        assert sql_dialect.drop_table(table="t", if_exists=True).query == 'DROP TABLE "t"'
        assert sql_dialect.drop_index(table="t", name="i", if_exists=True).query == 'DROP INDEX "i"'
        assert sql_dialect.create_index(table="t", name="i", columns=["a"], if_not_exists=True).query == (
            'CREATE INDEX "i" ON "t" ("a")'
        )

    def test_create_unique_partial_index_inlines_the_predicate(self, sql_dialect: SQLDialect) -> None:
        query_with_params = sql_dialect.create_index(
            table=["s", "t"],
            name="i",
            columns=["a", "b"],
            unique=True,
            where=[Condition(ConditionEnum.EQUALS, "deleted", False), Condition(ConditionEnum.NOT_EQUALS, "c", "it's")],
        )

        assert query_with_params.query == (
            'CREATE UNIQUE INDEX "i" ON "s"."t" ("a", "b") WHERE "deleted" = FALSE AND "c" <> \'it\'\'s\''
        )
        assert list(query_with_params.params) == []

    def test_create_index_requires_columns(self, sql_dialect: SQLDialect) -> None:
        with pytest.raises(QueryError, match="no index columns"):
            sql_dialect.create_index(table="t", name="i", columns=[])
