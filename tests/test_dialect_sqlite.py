"""SQLite statement generation, partly verified against a real in-memory database."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator

import pytest

from sentiencedb import Driver, QueryError
from sentiencedb.dialects import SQLiteDialect
from sentiencedb.options import Options
from sentiencedb.queries.enums import ConditionEnum, JoinEnum, TypeEnum, UnionEnum
from sentiencedb.queries.helpers import escape_backslash
from sentiencedb.queries.objects import (
    AddColumn,
    AddForeignKeyConstraint,
    AddPrimaryKeys,
    AddUniqueConstraint,
    Alter,
    AlterColumn,
    Column,
    Condition,
    ConditionNode,
    DropColumn,
    DropConstraint,
    ForeignKeyConstraint,
    JoinClause,
    OnConflict,
    RenameColumn,
    Type,
    UnionClause,
    UniqueConstraint,
)
from sentiencedb.queries.query import Query
from sentiencedb.queries.type_aliases import Scalar

StaticQuery = Callable[..., Query]


def where(dialect: SQLiteDialect, *conditions: ConditionNode) -> tuple[str, list[Scalar]]:
    query_with_params = dialect.select(table="t", where=conditions)

    return query_with_params.query.split(" WHERE ", 1)[1], list(query_with_params.params)


def sqlite(version: str, options: Options | None = None) -> SQLiteDialect:
    return SQLiteDialect(Driver.SQLITE, version, options)


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    yield connection
    connection.close()


class TestLikeAndGlob:
    @pytest.mark.parametrize(
        ("like", "glob"),
        [
            ("a%", "a*"),
            ("_b", "?b"),
            ("50\\%", "50%"),
            ("a\\_b", "a_b"),
            ("a\\\\b", "a\\b"),
            ("x*y?", "x[*]y[?]"),
            ("[a]", "[[]a[]]"),
            ("\\*\\[", "[*][[]"),
            ("\\q", "q"),
            ("trailing\\", "trailing\\"),
        ],
    )
    def test_case_sensitive_like_becomes_glob(self, sqlite_dialect: SQLiteDialect, like: str, glob: str) -> None:
        assert where(sqlite_dialect, Condition(ConditionEnum.LIKE, "c", like)) == ('"c" GLOB ?', [glob])

    def test_case_insensitive_like_declares_its_escape(self, sqlite_dialect: SQLiteDialect) -> None:
        assert where(sqlite_dialect, Condition(ConditionEnum.NOT_LIKE, "c", "a\\%", case_insensitive=True)) == (
            "\"c\" NOT LIKE ? ESCAPE '\\'",
            ["a\\%"],
        )

    def test_native_glob(self, sqlite_dialect: SQLiteDialect) -> None:
        assert where(sqlite_dialect, Condition(ConditionEnum.NOT_GLOB, "c", "a*")) == ('"c" NOT GLOB ?', ["a*"])
        assert where(sqlite_dialect, Condition(ConditionEnum.GLOB, "c", "A*", case_insensitive=True)) == (
            'lower("c") GLOB lower(?)',
            ["A*"],
        )

    @pytest.mark.parametrize(
        ("contains", "case_insensitive", "expected"),
        [
            ("50% o", False, ["50% off"]),
            ("a_b", False, ["a_b"]),
            ("a\\b", False, ["a\\b"]),
            ("[x]*", False, ["[x]*"]),
            ("50% o", True, ["50% off", "50% OFF"]),
            ("A_B", True, ["a_b"]),
            ("a\\b", True, ["a\\b"]),
        ],
    )
    def test_escaped_patterns_match_literally(
        self,
        sqlite_dialect: SQLiteDialect,
        connection: sqlite3.Connection,
        contains: str,
        case_insensitive: bool,
        expected: list[str],
    ) -> None:
        connection.execute('CREATE TABLE "t" ("c" TEXT)')
        connection.executemany(
            'INSERT INTO "t" VALUES (?)',
            [(c,) for c in ["50% off", "50% OFF", "500 off", "a_b", "axb", "a\\b", "[x]*"]],
        )
        pattern = "%" + escape_backslash(contains, ["%", "_", "-", "^", "[", "]"]) + "%"
        query_with_params = sqlite_dialect.select(
            table="t",
            columns=["c"],
            where=[Condition(ConditionEnum.LIKE, "c", pattern, case_insensitive=case_insensitive)],
        )

        rows = connection.execute(query_with_params.query, list(query_with_params.params)).fetchall()

        assert [row[0] for row in rows] == expected


class TestRegex:
    def test_regexp_like_function(self, sqlite_dialect: SQLiteDialect) -> None:
        assert where(sqlite_dialect, Condition(ConditionEnum.REGEX, "c", "^a", flags="i")) == (
            'regexp_like("c", ?, ?)',
            ["^a", "i"],
        )

    def test_regexp_operator_option(self) -> None:
        dialect = sqlite("3.45.1", {"use_regexp": True})

        assert where(dialect, Condition(ConditionEnum.NOT_REGEX, "c", "^a", flags="i")) == (
            '"c" NOT REGEXP ?',
            ["(?i)^a"],
        )


class TestSelect:
    def test_no_lateral_and_no_distinct_on(self, sqlite_dialect: SQLiteDialect) -> None:
        with pytest.raises(QueryError, match="LATERAL"):
            sqlite_dialect.select(table="t", joins=[JoinClause(JoinEnum.CROSS_JOIN_LATERAL, "x")])

        with pytest.raises(QueryError, match="DISTINCT ON"):
            sqlite_dialect.select(table="t", distinct=["a"])

    def test_join_without_conditions(self, sqlite_dialect: SQLiteDialect) -> None:
        assert sqlite_dialect.select(table="t", joins=[JoinClause(JoinEnum.LEFT_JOIN, "x")]).query == (
            'SELECT * FROM "t" LEFT JOIN "x" ON 1'
        )

    def test_unions_without_parenthesised_members(
        self, sqlite_dialect: SQLiteDialect, static_query: StaticQuery, connection: sqlite3.Connection
    ) -> None:
        connection.execute('CREATE TABLE "t" ("a" INTEGER)')
        connection.execute('INSERT INTO "t" VALUES (1), (2), (3)')
        query_with_params = sqlite_dialect.select(
            table="t",
            where=[Condition(ConditionEnum.EQUALS, "a", 1)],
            unions=[UnionClause(UnionEnum.UNION_ALL, static_query('SELECT * FROM "t" ORDER BY "a" DESC LIMIT ?', [1]))],
        )

        assert query_with_params.query == (
            'SELECT * FROM (SELECT * FROM "t" WHERE "a" = ?)'
            ' UNION ALL SELECT * FROM (SELECT * FROM "t" ORDER BY "a" DESC LIMIT ?)'
        )
        assert connection.execute(query_with_params.query, list(query_with_params.params)).fetchall() == [(1,), (3,)]


class TestInsert:
    def test_upsert_and_returning(self, sqlite_dialect: SQLiteDialect, connection: sqlite3.Connection) -> None:
        connection.execute('CREATE TABLE "t" ("id" INTEGER PRIMARY KEY, "email" TEXT UNIQUE, "n" INTEGER)')
        connection.execute("INSERT INTO \"t\" VALUES (1, 'a', 1)")
        query_with_params = sqlite_dialect.insert(
            table="t",
            values=[{"email": "a", "n": 5}],
            on_conflict=OnConflict(["email"], {}),
            returning=["id", "n"],
        )

        assert query_with_params.query == (
            'INSERT INTO "t" ("email", "n") VALUES (?, ?) ON CONFLICT ("email")'
            ' DO UPDATE SET "email" = excluded."email", "n" = excluded."n" RETURNING "id", "n"'
        )
        assert connection.execute(query_with_params.query, list(query_with_params.params)).fetchall() == [(1, 5)]

    def test_rows_with_the_same_columns(self, sqlite_dialect: SQLiteDialect, connection: sqlite3.Connection) -> None:
        connection.execute('CREATE TABLE "t" ("a" INTEGER, "b" INTEGER DEFAULT 7)')
        query_with_params = sqlite_dialect.insert(table="t", values=[{"a": 1, "b": 2}, {"b": 4, "a": 3}])
        connection.execute(query_with_params.query, list(query_with_params.params))

        assert query_with_params.query == 'INSERT INTO "t" ("a", "b") VALUES (?, ?), (?, ?)'
        assert connection.execute('SELECT * FROM "t"').fetchall() == [(1, 2), (3, 4)]

    def test_default_in_values_is_unsupported(self, sqlite_dialect: SQLiteDialect) -> None:
        with pytest.raises(QueryError, match="DEFAULT in VALUES"):
            sqlite_dialect.insert(table="t", values=[{"a": 1, "b": 2}, {"a": 3}])

    def test_named_constraints_are_unsupported(self, sqlite_dialect: SQLiteDialect) -> None:
        with pytest.raises(QueryError, match="named constraints"):
            sqlite_dialect.insert(table="t", values=[{"a": 1}], on_conflict=OnConflict("uq_a"))

    def test_version_gates(self) -> None:
        old = sqlite("3.23.1")

        assert old.insert(table="t", values=[{"a": 1}], on_conflict=OnConflict(["a"]), returning=[]).query == (
            'INSERT INTO "t" ("a") VALUES (?)'
        )
        assert sqlite("3.34.1").delete(table="t", returning=[]).query == 'DELETE FROM "t"'
        assert sqlite("3.35.0").delete(table="t", returning=[]).query == 'DELETE FROM "t" RETURNING *'


class TestCreateTable:
    def test_identity_constraints_and_strict(self, connection: sqlite3.Connection) -> None:
        dialect = sqlite("3.45.1", {"strict": True})
        query_with_params = dialect.create_table(
            table="t",
            columns=[
                Column("id", Type(TypeEnum.INTEGER, 64), not_null=True, generated_by_default_as_identity=True),
                Column("email", "TEXT", not_null=True, default="x"),
                Column("parent", "INTEGER"),
            ],
            primary_keys=["id"],
            constraints=[UniqueConstraint(["email"], "uq_email"), ForeignKeyConstraint(["parent"], "t", ["id"], "fk")],
            if_not_exists=True,
        )

        assert query_with_params.query == (
            'CREATE TABLE IF NOT EXISTS "t" ("id" INTEGER PRIMARY KEY AUTOINCREMENT,'
            ' "email" TEXT NOT NULL DEFAULT \'x\', "parent" INTEGER, UNIQUE ("email"),'
            ' FOREIGN KEY ("parent") REFERENCES "t" ("id")) STRICT'
        )
        connection.execute(query_with_params.query)

    def test_composite_primary_key_and_types(self, sqlite_dialect: SQLiteDialect) -> None:
        query_with_params = sqlite_dialect.create_table(
            table="t",
            columns=[
                Column("a", Type(TypeEnum.BOOLEAN), default=True),
                Column("b", Type(TypeEnum.FLOAT, 64)),
            ],
            primary_keys=["a", "b"],
        )

        assert query_with_params.query == 'CREATE TABLE "t" ("a" BOOLEAN DEFAULT 1, "b" REAL, PRIMARY KEY ("a", "b"))'

    def test_if_not_exists_needs_3_3(self) -> None:
        assert sqlite("3.2.8").create_table(table="t", columns=[Column("a", "INT")], if_not_exists=True).query == (
            'CREATE TABLE "t" ("a" INT)'
        )


class TestAlterTable:
    def test_supported_alters(self, sqlite_dialect: SQLiteDialect) -> None:
        statements = sqlite_dialect.alter_table(
            table="t", alters=[AddColumn("a", Type(TypeEnum.TEXT)), RenameColumn("a", "b"), DropColumn("b")]
        )

        assert [statement.query for statement in statements] == [
            'ALTER TABLE "t" ADD COLUMN "a" TEXT',
            'ALTER TABLE "t" RENAME COLUMN "a" TO "b"',
            'ALTER TABLE "t" DROP COLUMN "b"',
        ]

    @pytest.mark.parametrize(
        "alter",
        [
            AlterColumn("a", "TEXT"),
            AddPrimaryKeys(["a"]),
            AddUniqueConstraint(["a"]),
            AddForeignKeyConstraint(["a"], "o", ["id"]),
            DropConstraint("uq"),
        ],
    )
    def test_unsupported_alters(self, sqlite_dialect: SQLiteDialect, alter: Alter) -> None:
        with pytest.raises(QueryError, match="SQLite does not support"):
            sqlite_dialect.alter_table(table="t", alters=[alter])

    def test_rename_column_needs_3_25(self) -> None:
        with pytest.raises(QueryError, match="renaming columns"):
            sqlite("3.24.0").alter_table(table="t", alters=[RenameColumn("a", "b")])


class TestIndexes:
    def test_partial_unique_index(self, sqlite_dialect: SQLiteDialect, connection: sqlite3.Connection) -> None:
        connection.execute('CREATE TABLE "t" ("a" TEXT, "deleted" BOOLEAN)')
        query_with_params = sqlite_dialect.create_index(
            table="t",
            name="i",
            columns=["a"],
            unique=True,
            if_not_exists=True,
            where=[Condition(ConditionEnum.EQUALS, "deleted", False)],
        )

        assert query_with_params.query == 'CREATE UNIQUE INDEX IF NOT EXISTS "i" ON "t" ("a") WHERE "deleted" = 0'
        connection.execute(query_with_params.query)
        connection.execute(sqlite_dialect.drop_index(table="t", name="i", if_exists=True).query)
        connection.execute(sqlite_dialect.drop_index(table="t", name="i", if_exists=True).query)

    def test_if_exists_needs_3_3(self) -> None:
        assert sqlite("3.2.8").drop_index(table="t", name="i", if_exists=True).query == 'DROP INDEX "i"'
