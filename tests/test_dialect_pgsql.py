"""PostgreSQL statement generation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest

from sentiencedb import Driver, QueryError
from sentiencedb.dialects import PgSQLDialect
from sentiencedb.options import Options
from sentiencedb.queries.enums import ConditionEnum, JoinEnum, TypeEnum
from sentiencedb.queries.objects import (
    AlterColumn,
    Column,
    Condition,
    ConditionNode,
    DropConstraint,
    JoinClause,
    OnConflict,
    QueryWithParams,
    Raw,
    SubQuery,
    Type,
    UniqueConstraint,
)
from sentiencedb.queries.query import Query
from sentiencedb.queries.type_aliases import Scalar

StaticQuery = Callable[..., Query]


def where(dialect: PgSQLDialect, *conditions: ConditionNode) -> tuple[str, list[Scalar]]:
    query_with_params = dialect.select(table="t", where=conditions)

    return query_with_params.query.split(" WHERE ", 1)[1], list(query_with_params.params)


def pgsql(version: str, options: Options | None = None) -> PgSQLDialect:
    return PgSQLDialect(Driver.PGSQL, version, options)


class TestEscaping:
    def test_string_literals_double_quotes_and_keep_backslashes(self, pgsql_dialect: PgSQLDialect) -> None:
        """PHP escaped PostgreSQL strings with backslashes, which PostgreSQL keeps literally (bug A1)."""
        query_with_params = pgsql_dialect.create_table(
            table="t", columns=[Column("path", Type(TypeEnum.TEXT), default="C:\\dir\\it's\n")]
        )

        assert query_with_params.query == "CREATE TABLE \"t\" (\"path\" TEXT DEFAULT 'C:\\dir\\it''s\n')"
        assert QueryWithParams("SELECT ?", ["a\\'b"]).to_sql(pgsql_dialect) == "SELECT 'a\\''b'"

    def test_identifiers_double_the_quote(self, pgsql_dialect: PgSQLDialect) -> None:
        assert pgsql_dialect.drop_table(table=["s", 'we"ird']).query == 'DROP TABLE "s"."we""ird"'


class TestConditions:
    def test_like_and_ilike(self, pgsql_dialect: PgSQLDialect) -> None:
        assert where(pgsql_dialect, Condition(ConditionEnum.LIKE, "c", "%50\\%%")) == ('"c" LIKE ?', ["%50\\%%"])
        assert where(pgsql_dialect, Condition(ConditionEnum.NOT_LIKE, "c", "a%")) == ('"c" NOT LIKE ?', ["a%"])
        assert where(pgsql_dialect, Condition(ConditionEnum.LIKE, "c", "a%", case_insensitive=True)) == (
            '"c" ILIKE ?',
            ["a%"],
        )
        assert where(pgsql_dialect, Condition(ConditionEnum.NOT_LIKE, "c", "a%", case_insensitive=True)) == (
            '"c" NOT ILIKE ?',
            ["a%"],
        )

    def test_glob_becomes_ilike(self, pgsql_dialect: PgSQLDialect) -> None:
        assert where(pgsql_dialect, Condition(ConditionEnum.NOT_GLOB, "c", "a*_", case_insensitive=True)) == (
            '"c" NOT ILIKE ?',
            ["a%\\_"],
        )

    def test_regexp_like_from_15(self, pgsql_dialect: PgSQLDialect) -> None:
        assert where(pgsql_dialect, Condition(ConditionEnum.REGEX, "c", "^a", flags="i")) == (
            'regexp_like("c", ?, ?)',
            ["^a", "i"],
        )

    @pytest.mark.parametrize("dialect", [pgsql("14.10"), pgsql("18.0", {"use_tilde_regex": True})])
    def test_tilde_operators(self, dialect: PgSQLDialect) -> None:
        assert where(dialect, Condition(ConditionEnum.REGEX, "c", "^a", flags="i")) == ('"c" ~ ?', ["(?i)^a"])
        assert where(dialect, Condition(ConditionEnum.NOT_REGEX, "c", "^a")) == ('"c" !~ ?', ["^a"])

    def test_cast_types(self, pgsql_dialect: PgSQLDialect) -> None:
        assert where(pgsql_dialect, Condition(ConditionEnum.EQUALS, "c", 1.5, cast=True)) == (
            'cast("c" AS DOUBLE PRECISION) = cast(? AS DOUBLE PRECISION)',
            [1.5],
        )
        moment = datetime(2024, 1, 2)
        assert where(pgsql_dialect, Condition(ConditionEnum.EQUALS, "c", moment, cast=True)) == (
            'cast("c" AS TIMESTAMP) = cast(? AS TIMESTAMP)',
            [moment],
        )


class TestSelect:
    def test_distinct_on(self, pgsql_dialect: PgSQLDialect) -> None:
        assert pgsql_dialect.select(table="t", distinct=["a", ["t", "b"]]).query == (
            'SELECT DISTINCT ON ("a", "t"."b") * FROM "t"'
        )

    def test_distinct_on_needs_7_2(self) -> None:
        with pytest.raises(QueryError, match="DISTINCT ON"):
            pgsql("7.1").select(table="t", distinct=["a"])

    def test_lateral_joins(self, pgsql_dialect: PgSQLDialect, static_query: StaticQuery) -> None:
        query_with_params = pgsql_dialect.select(
            table="u",
            joins=[
                JoinClause(JoinEnum.LEFT_JOIN_LATERAL, SubQuery(static_query("SELECT ?", [1]), "a")),
                JoinClause(JoinEnum.CROSS_JOIN_LATERAL, SubQuery(static_query("SELECT 2"), "b")),
            ],
        )

        assert query_with_params.query == (
            'SELECT * FROM "u" LEFT JOIN LATERAL (SELECT ?) AS "a" ON TRUE CROSS JOIN LATERAL (SELECT 2) AS "b"'
        )

    def test_lateral_needs_9_3(self) -> None:
        with pytest.raises(QueryError, match="LATERAL"):
            pgsql("9.2.24").select(table="u", joins=[JoinClause(JoinEnum.INNER_JOIN_LATERAL, "x")])


class TestUpsertAndReturning:
    def test_do_nothing(self, pgsql_dialect: PgSQLDialect) -> None:
        query_with_params = pgsql_dialect.insert(
            table="t", values=[{"a": 1}], on_conflict=OnConflict(["a", "b"]), returning=[]
        )

        assert query_with_params.query == (
            'INSERT INTO "t" ("a") VALUES (?) ON CONFLICT ("a", "b") DO NOTHING RETURNING *'
        )

    def test_update_every_inserted_column(self, pgsql_dialect: PgSQLDialect) -> None:
        query_with_params = pgsql_dialect.insert(
            table="users",
            values=[{"email": "a"}, {"email": "b", "name": "c"}],
            on_conflict=OnConflict(["email"], {}),
            returning=["id"],
        )

        assert query_with_params.query == (
            'INSERT INTO "users" ("email", "name") VALUES (?, DEFAULT), (?, ?) ON CONFLICT ("email")'
            ' DO UPDATE SET "email" = excluded."email", "name" = excluded."name" RETURNING "id"'
        )
        assert list(query_with_params.params) == ["a", "b", "c"]

    def test_update_explicitly_on_a_named_constraint(self, pgsql_dialect: PgSQLDialect) -> None:
        query_with_params = pgsql_dialect.insert(
            table="t",
            values=[{"a": 1, "n": 2}],
            on_conflict=OnConflict("t_a_key", {"n": Raw('"t"."n" + excluded."n"'), "at": None}),
        )

        assert query_with_params.query == (
            'INSERT INTO "t" ("a", "n") VALUES (?, ?) ON CONFLICT ON CONSTRAINT "t_a_key"'
            ' DO UPDATE SET "n" = "t"."n" + excluded."n", "at" = ?'
        )
        assert list(query_with_params.params) == [1, 2, None]

    def test_old_versions_ignore_upserts_and_returning(self) -> None:
        dialect = pgsql("8.1")

        assert dialect.insert(table="t", values=[{"a": 1}], on_conflict=OnConflict(["a"]), returning=[]).query == (
            'INSERT INTO "t" ("a") VALUES (?)'
        )

        with pytest.raises(QueryError, match="RETURNING is not supported on DELETE"):
            dialect.delete(table="t", returning=[])

    def test_update_and_delete_returning(self, pgsql_dialect: PgSQLDialect) -> None:
        assert pgsql_dialect.update(table="t", updates={"a": 1}, returning=["a", "b"]).query == (
            'UPDATE "t" SET "a" = ? RETURNING "a", "b"'
        )
        assert pgsql_dialect.delete(table="t", returning=[]).query == 'DELETE FROM "t" RETURNING *'


class TestCreateTable:
    def test_identity_columns_become_serials_by_default(self, pgsql_dialect: PgSQLDialect) -> None:
        query_with_params = pgsql_dialect.create_table(
            table="t",
            columns=[
                Column("id", Type(TypeEnum.INTEGER, 64), not_null=True, generated_by_default_as_identity=True),
                Column("n", Type(TypeEnum.INTEGER, 32), generated_by_default_as_identity=True),
                Column("s", "smallint", generated_by_default_as_identity=True),
                Column("b", "int8", generated_by_default_as_identity=True),
                Column("u", "UUID", generated_by_default_as_identity=True),
            ],
            primary_keys=["id"],
            if_not_exists=True,
        )

        assert query_with_params.query == (
            'CREATE TABLE IF NOT EXISTS "t" ("id" BIGSERIAL NOT NULL, "n" SERIAL, "s" serial, "b" bigserial,'
            ' "u" UUID, PRIMARY KEY ("id"))'
        )

    def test_identity_columns_without_serials(self) -> None:
        columns = [Column("id", Type(TypeEnum.INTEGER, 64), not_null=True, generated_by_default_as_identity=True)]

        assert pgsql("17.2", {"use_serials": False}).create_table(table="t", columns=columns).query == (
            'CREATE TABLE "t" ("id" BIGINT GENERATED BY DEFAULT AS IDENTITY NOT NULL)'
        )
        assert pgsql("9.6.24", {"use_serials": False}).create_table(table="t", columns=columns).query == (
            'CREATE TABLE "t" ("id" BIGSERIAL NOT NULL)'
        )

    def test_types_and_defaults(self, pgsql_dialect: PgSQLDialect) -> None:
        query_with_params = pgsql_dialect.create_table(
            table="t",
            columns=[
                Column("flag", Type(TypeEnum.BOOLEAN), not_null=True, default=False),
                Column("at", Type(TypeEnum.DATETIME, 6), default=datetime(2024, 1, 2)),
                Column("bin", "BYTEA", default=b"\x01"),
            ],
            constraints=[UniqueConstraint(["flag"], "uq")],
        )

        assert query_with_params.query == (
            'CREATE TABLE "t" ("flag" BOOLEAN NOT NULL DEFAULT FALSE,'
            " \"at\" TIMESTAMP DEFAULT '2024-01-02 00:00:00.000000', \"bin\" BYTEA DEFAULT '\\x01'::bytea,"
            ' CONSTRAINT "uq" UNIQUE ("flag"))'
        )

    def test_if_not_exists_needs_9_1(self) -> None:
        columns = [Column("a", "INT")]

        assert pgsql("9.0.23").create_table(table="t", columns=columns, if_not_exists=True).query == (
            'CREATE TABLE "t" ("a" INT)'
        )
        assert pgsql("9.1.0").drop_table(table="t", if_exists=True).query == 'DROP TABLE IF EXISTS "t"'


class TestAlterAndIndexes:
    def test_alters(self, pgsql_dialect: PgSQLDialect) -> None:
        statements = pgsql_dialect.alter_table(
            table="t", alters=[AlterColumn("a", "SET NOT NULL"), DropConstraint("uq")]
        )

        assert [statement.query for statement in statements] == [
            'ALTER TABLE "t" ALTER COLUMN "a" SET NOT NULL',
            'ALTER TABLE "t" DROP CONSTRAINT "uq"',
        ]

    def test_index_if_not_exists_from_9_5(self, pgsql_dialect: PgSQLDialect) -> None:
        assert pgsql_dialect.create_index(table="t", name="i", columns=["a"], if_not_exists=True).query == (
            'CREATE INDEX IF NOT EXISTS "i" ON "t" ("a")'
        )
        assert pgsql_dialect.drop_index(table="t", name="i", if_exists=True).query == 'DROP INDEX IF EXISTS "i"'
        assert pgsql("9.4.26").drop_index(table="t", name="i", if_exists=True).query == 'DROP INDEX "i"'

    def test_partial_index_literals(self, pgsql_dialect: PgSQLDialect) -> None:
        query_with_params = pgsql_dialect.create_index(
            table="t", name="i", columns=["a"], where=[Condition(ConditionEnum.EQUALS, "active", True)]
        )

        assert query_with_params.query == 'CREATE INDEX "i" ON "t" ("a") WHERE "active" = TRUE'
