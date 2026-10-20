"""Regressions for the dialect and schema review (finding IDs in the test names' docstrings).

The unit tests need no database. The live tests run on SQLite and on every server that is reachable (``docker compose
up -d``), in a dedicated ``sentiencedb_regressions`` database.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from sentiencedb import Database, Driver, QueryError, connect_mariadb, connect_mysql, connect_postgresql, connect_sqlite
from sentiencedb.dialects import MySQLDialect, PgSQLDialect, SQLDialect, SQLiteDialect
from sentiencedb.exceptions import DriverError
from sentiencedb.queries.enums import ConditionEnum, TypeEnum
from sentiencedb.queries.objects import Column, Condition, DropColumn, DropConstraint, OnConflict, Type

DATABASE = "sentiencedb_regressions"

SERVERS = {
    Driver.PGSQL: (5432, "postgres", "postgres", "psycopg"),
    Driver.MYSQL: (3306, "mysql", "root", "mysql.connector"),
    Driver.MARIADB: (3307, "mysql", "root", "mysql.connector"),
}


def where(dialect: SQLDialect, condition: Condition) -> tuple[str, list[Any]]:
    query_with_params = dialect.select(table="t", where=[condition])

    return query_with_params.query.removeprefix('SELECT * FROM "t" WHERE ').removeprefix(
        "SELECT * FROM `t` WHERE "
    ), list(query_with_params.params)


# -- unit ---------------------------------------------------------------------------------------------------------


def test_h1_mariadb_10_handshake_version() -> None:
    """MariaDB 10.x reports ``5.5.5-10.x``; its version gates must see 10.x."""
    dialect = MySQLDialect(Driver.MARIADB, "5.5.5-10.11.19-MariaDB-ubu2204")

    assert dialect.version == 101119
    assert dialect.returning
    assert dialect.alter_table(table="c", alters=[DropConstraint("fk_p")])[0].query == (
        "ALTER TABLE `c` DROP CONSTRAINT `fk_p`"
    )


class TestM1CastTypes:
    def test_mysql_float_is_not_a_bare_decimal(self) -> None:
        condition = Condition(ConditionEnum.EQUALS, "c", 9.95, cast=True)

        assert where(MySQLDialect(Driver.MYSQL, "8.0.17"), condition)[0] == "cast(`c` AS DOUBLE) = cast(? AS DOUBLE)"
        assert where(MySQLDialect(Driver.MARIADB, "10.4.5"), condition)[0] == "cast(`c` AS DOUBLE) = cast(? AS DOUBLE)"

        for dialect in (MySQLDialect(Driver.MYSQL, "8.0.16"), MySQLDialect(Driver.MARIADB, "10.4.4")):
            assert where(dialect, condition)[0] == "cast(`c` AS DECIMAL(65, 30)) = cast(? AS DECIMAL(65, 30))"

    def test_sqlite_datetime_casts_to_text(self, sqlite_dialect: SQLiteDialect) -> None:
        condition = Condition(ConditionEnum.EQUALS, "c", datetime(2024, 5, 1), cast=True)

        assert where(sqlite_dialect, condition)[0] == 'cast("c" AS TEXT) = cast(? AS TEXT)'


class TestM4Glob:
    @pytest.mark.parametrize(
        ("pattern", "like"),
        [
            ("a*b?", "a%b_"),
            ("Star[*]", "Star*"),
            ("[[]x", "[x"),
            ("100%_[?]", "100\\%\\_?"),
            ("a\\b", "a\\\\b"),
        ],
    )
    def test_patterns_without_real_classes_stay_like(
        self, pgsql_dialect: PgSQLDialect, pattern: str, like: str
    ) -> None:
        assert where(pgsql_dialect, Condition(ConditionEnum.GLOB, "c", pattern)) == ('"c" LIKE ?', [like])

    @pytest.mark.parametrize(
        ("pattern", "regex"),
        [
            ("[ab]*", "^[ab].*$"),
            ("[^a-c]?", "^[^a-c].$"),
            ("[]x]", "^[\\]x]$"),
            ("[a-]", "^[a\\-]$"),
            ("[a-c-e]", "^[a-c\\-e]$"),
            ("x.[$^]", "^x\\.[\\$\\^]$"),
        ],
    )
    def test_character_classes_become_an_anchored_regex(
        self, pgsql_dialect: PgSQLDialect, pattern: str, regex: str
    ) -> None:
        assert where(pgsql_dialect, Condition(ConditionEnum.GLOB, "c", pattern)) == (
            'regexp_like("c", ?, ?)',
            [regex, "s"],
        )

    def test_case_insensitive_and_negated(self, pgsql_dialect: PgSQLDialect, mysql_dialect: MySQLDialect) -> None:
        condition = Condition(ConditionEnum.NOT_GLOB, "c", "[ab]*", case_insensitive=True)

        assert where(pgsql_dialect, condition) == ('NOT regexp_like("c", ?, ?)', ["^[ab].*$", "is"])
        assert where(mysql_dialect, condition) == ("NOT regexp_like(`c`, ?, 'cin')", ["^[ab].*$"])

    def test_unterminated_class(self, pgsql_dialect: PgSQLDialect) -> None:
        with pytest.raises(QueryError, match="unterminated"):
            where(pgsql_dialect, Condition(ConditionEnum.GLOB, "c", "[ab"))


def test_l3_unknown_regex_flags_raise(sqlite_dialect: SQLiteDialect, sql_dialect: SQLDialect) -> None:
    for dialect in (sqlite_dialect, sql_dialect, SQLiteDialect(Driver.SQLITE, "3.45.1", {"use_regexp": True})):
        with pytest.raises(QueryError, match="unsupported regex flags 'x'"):
            where(dialect, Condition(ConditionEnum.REGEX, "c", "a", flags="ix"))


class TestM5Strict:
    def test_portable_types_map_to_strict_types(self) -> None:
        dialect = SQLiteDialect(Driver.SQLITE, "3.45.1", {"strict": True})
        columns = [Column(type.value, Type(type, 64)) for type in TypeEnum]

        assert dialect.create_table(table="t", columns=columns).query == (
            'CREATE TABLE "t" ("boolean" INTEGER, "integer" INTEGER, "float" REAL, "varchar" TEXT, "text" TEXT,'
            ' "datetime" TEXT) STRICT'
        )

    def test_strict_needs_3_37(self) -> None:
        with pytest.raises(QueryError, match=r"3\.37\.0"):
            SQLiteDialect(Driver.SQLITE, "3.36.0", {"strict": True}).create_table(
                table="t", columns=[Column("a", "INT")]
            )


def test_l4_index_statements_follow_the_table_schema(
    pgsql_dialect: PgSQLDialect, sqlite_dialect: SQLiteDialect, mysql_dialect: MySQLDialect
) -> None:
    assert pgsql_dialect.create_index(table=["s", "t"], name="i", columns=["a"]).query == (
        'CREATE INDEX "i" ON "s"."t" ("a")'
    )
    assert pgsql_dialect.drop_index(table=["s", "t"], name="i", if_exists=True).query == 'DROP INDEX IF EXISTS "s"."i"'
    assert pgsql_dialect.drop_index(table="t", name="i").query == 'DROP INDEX "i"'
    assert sqlite_dialect.create_index(table=["main", "t"], name="i", columns=["a"]).query == (
        'CREATE INDEX "main"."i" ON "t" ("a")'
    )
    assert sqlite_dialect.drop_index(table=["main", "t"], name="i").query == 'DROP INDEX "main"."i"'
    assert mysql_dialect.drop_index(table=["s", "t"], name="i").query == "DROP INDEX `i` ON `s`.`t`"


class TestL5EmptyConflictTarget:
    def test_do_nothing_has_no_target(self, pgsql_dialect: PgSQLDialect, sqlite_dialect: SQLiteDialect) -> None:
        for dialect in (pgsql_dialect, sqlite_dialect):
            query = dialect.insert(table="t", values=[{"a": 1}], on_conflict=OnConflict([])).query

            assert query.endswith("VALUES (?) ON CONFLICT DO NOTHING")

    def test_do_update_needs_a_target(self, pgsql_dialect: PgSQLDialect) -> None:
        with pytest.raises(QueryError, match="DO UPDATE requires"):
            pgsql_dialect.insert(table="t", values=[{"a": 1}], on_conflict=OnConflict([], {}))


def test_l6_sqlite_drop_column_needs_3_35() -> None:
    assert SQLiteDialect(Driver.SQLITE, "3.35.0").alter_table(table="t", alters=[DropColumn("a")])[0].query == (
        'ALTER TABLE "t" DROP COLUMN "a"'
    )

    with pytest.raises(QueryError, match=r"3\.35\.0"):
        SQLiteDialect(Driver.SQLITE, "3.34.1").alter_table(table="t", alters=[DropColumn("a")])


def test_l1_sqlite_case_sensitive_like_option_compares_lowercase() -> None:
    dialect = SQLiteDialect(Driver.SQLITE, "3.45.1", {"deprecated_case_sensitive_like": True})

    assert where(dialect, Condition(ConditionEnum.LIKE, "c", "a%", case_insensitive=True))[0] == (
        "lower(\"c\") LIKE lower(?) ESCAPE '\\'"
    )


def test_s4_returning(pgsql_dialect: PgSQLDialect) -> None:
    """``INSERT`` drops an unsupported ``RETURNING`` (builders emulate it); ``UPDATE``/``DELETE`` raise."""
    old = PgSQLDialect(Driver.PGSQL, "8.1")

    assert old.insert(table="t", values=[{"a": 1}], returning=[]).query == 'INSERT INTO "t" ("a") VALUES (?)'
    assert pgsql_dialect.delete(table="t", returning=["a"]).query == 'DELETE FROM "t" RETURNING "a"'

    with pytest.raises(QueryError, match="DELETE"):
        old.delete(table="t", returning=[])

    with pytest.raises(QueryError, match="UPDATE"):
        MySQLDialect(Driver.MARIADB, "11.4.4").update(table="t", updates={"a": 1}, returning=[])


# -- live ---------------------------------------------------------------------------------------------------------


def _server_is_up(port: int) -> bool:
    try:
        socket.create_connection(("localhost", port), timeout=0.5).close()
    except OSError:
        return False

    return True


def _connect(driver: Driver, name: str = DATABASE, **kwargs: Any) -> Database:
    port, _, username, _ = SERVERS[driver]
    connect = {
        Driver.PGSQL: connect_postgresql,
        Driver.MYSQL: connect_mysql,
        Driver.MARIADB: connect_mariadb,
    }[driver]

    return connect(name, port=port, username=kwargs.pop("username", username), **kwargs)


@pytest.fixture(scope="module", params=[Driver.SQLITE, *SERVERS], ids=lambda driver: driver.value)
def driver(request: pytest.FixtureRequest) -> Iterator[Driver]:
    driver: Driver = request.param

    if driver is Driver.SQLITE:
        yield driver
        return

    port, admin_database, _, module = SERVERS[driver]
    pytest.importorskip(module)

    if not _server_is_up(port):
        pytest.skip(f"{driver.value} is not reachable on localhost:{port}")

    with _connect(driver, admin_database) as admin:
        admin.exec(f"DROP DATABASE IF EXISTS {DATABASE}")
        admin.exec(f"CREATE DATABASE {DATABASE}")

    yield driver

    with _connect(driver, admin_database) as admin:
        admin.exec(f"DROP DATABASE {DATABASE}")


@pytest.fixture
def connect(driver: Driver) -> Iterator[Callable[..., Database]]:
    """Connects to the (reset) test database; ``connect(queries=[..], options={..})``."""
    opened: list[Database] = []

    def open_database(**kwargs: Any) -> Database:
        db = connect_sqlite(":memory:", **kwargs) if driver is Driver.SQLITE else _connect(driver, **kwargs)
        opened.append(db)

        return db

    if driver is Driver.PGSQL:
        with _connect(driver) as db:
            db.exec("DROP SCHEMA public CASCADE")
            db.exec("CREATE SCHEMA public")
            db.exec("DROP SCHEMA IF EXISTS other CASCADE")
    elif driver is not Driver.SQLITE:
        with _connect(driver) as db:
            db.exec(f"DROP DATABASE {DATABASE}")
            db.exec(f"CREATE DATABASE {DATABASE}")

    yield open_database

    for db in opened:
        db.close()


@pytest.fixture
def db(connect: Callable[..., Database]) -> Database:
    return connect()


def ids(query: Any, emulate_prepare: bool = False) -> list[int]:
    return sorted(row["id"] for row in query.columns(["id"]).execute(emulate_prepare).fetch_dicts())


def only(db: Database, *drivers: Driver) -> None:
    if db.dialect.driver not in drivers:
        pytest.skip(f"only on {', '.join(driver.value for driver in drivers)}")


@pytest.mark.parametrize("emulate_prepare", [False, True])
def test_m1_cast_equals(db: Database, emulate_prepare: bool) -> None:
    (
        db.create_table("p")
        .integer("id")
        .column("price", "DECIMAL(10, 2)")
        .column("at", "VARCHAR(40)" if db.dialect.driver is Driver.SQLITE else "TIMESTAMP")
        .execute()
    )
    db.insert("p").values({"id": 1, "price": Decimal("9.95"), "at": datetime(2024, 5, 1, 10)}).execute()
    db.insert("p").values({"id": 2, "price": Decimal("10.40"), "at": datetime(2024, 12, 31, 23, 59, 59)}).execute()

    assert ids(db.select("p").where_equals("at", datetime(2024, 5, 1, 10), cast=True), emulate_prepare) == [1]

    assert ids(db.select("p").where_equals("price", 9.95, cast=True), emulate_prepare) == [1]


class TestM2Literals:
    VALUE = "nobody\\' OR 1=1 -- "

    @pytest.mark.parametrize("value", ["it's", "back\\slash", "\\'", "end\\", "tab\tnew\nline", "%_?"])
    def test_emulated_literals_round_trip(self, db: Database, value: str) -> None:
        db.create_table("s").integer("id").varchar("v").execute()
        db.insert("s").values({"id": 1, "v": value}).execute(emulate_prepare=True)

        assert db.select("s").columns(["v"]).execute().scalar() == value
        assert ids(db.select("s").where_equals("v", value), emulate_prepare=True) == [1]

    def test_no_injection_under_non_default_string_settings(self, connect: Callable[..., Database]) -> None:
        driver = connect().dialect.driver
        queries = {
            Driver.PGSQL: ["SET standard_conforming_strings = off", "SET escape_string_warning = off"],
            Driver.MYSQL: ["SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES')"],
            Driver.MARIADB: ["SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES')"],
        }.get(driver, [])
        db = connect(queries=queries)
        db.create_table("secrets").integer("id").varchar("owner").execute()
        db.insert("secrets").values({"id": 1, "owner": "alice"}).execute()
        db.insert("secrets").values({"id": 2, "owner": "bob"}).execute()

        assert ids(db.select("secrets").where_equals("owner", self.VALUE), emulate_prepare=True) == []
        assert ids(db.select("secrets").where_equals("owner", "it's"), emulate_prepare=True) == []


class TestM3DoNothing:
    def test_only_duplicate_keys_are_ignored(self, db: Database) -> None:
        db.create_table("u").integer("id").varchar("email", 5).varchar("name", 5, not_null=True).primary_keys(
            ["id"]
        ).unique_constraint(["email"]).execute()
        db.insert("u").values({"id": 1, "email": "a", "name": "x"}).execute()
        db.insert("u").values({"id": 2, "email": "a", "name": "y"}).on_conflict_do_nothing(["email"]).execute()

        assert db.select("u").columns(["id", "name"]).execute().fetch_dicts() == [{"id": 1, "name": "x"}]

        with pytest.raises(DriverError):
            db.insert("u").values({"id": 3, "email": "b", "name": None}).on_conflict_do_nothing(["email"]).execute()

        if db.dialect.driver is not Driver.SQLITE:  # SQLite does not enforce VARCHAR lengths
            with pytest.raises(DriverError):
                db.insert("u").values({"id": 4, "email": "c", "name": "too long"}).on_conflict_do_nothing(
                    ["email"]
                ).execute()

        assert db.select("u").count() == 1

    def test_l5_no_conflict_columns(self, db: Database) -> None:
        db.create_table("u").integer("id").primary_keys(["id"]).execute()
        db.insert("u").values({"id": 1}).execute()
        db.insert("u").values({"id": 1}).on_conflict_do_nothing([]).execute()

        assert db.select("u").count() == 1


GLOB_VALUES = ["abc", "]x", "Star*", "?", "a\\b", "-", "&", "[", "$", "^", ".", "d", "line\nbreak", "ÄB", "b", "B"]
GLOB_PATTERNS = ["[ab]*", "Star[*]", "[]]*", "[^a-c]*", "*[*]", "[?]", "a[\\]b", "[-a]", "[a-]", "[&]", "[[]", "[$^.]"]
GLOB_PATTERNS += ["[a-c-e]", "*[\n]*", "[A-Z]", "a*", "?", "*"]


@pytest.mark.parametrize("case_insensitive", [False, True])
def test_m4_glob_matches_sqlite(db: Database, case_insensitive: bool) -> None:
    """SQLite's native ``GLOB`` is the oracle for the translation on the other engines. Case insensitive matching of
    non-ASCII text follows each engine's collation (MySQL's also ignores accents), so it is compared on ASCII only."""
    oracle = connect_sqlite(":memory:")
    compared = {index for index, value in enumerate(GLOB_VALUES) if value.isascii() or not case_insensitive}

    def matches(query: Any, emulate_prepare: bool = False) -> list[int]:
        return [index for index in ids(query, emulate_prepare) if index in compared]

    for target in (db, oracle):
        target.create_table("g").integer("id").varchar("v", 20).execute()

        for index, value in enumerate(GLOB_VALUES):
            target.insert("g").values({"id": index, "v": value}).execute()

    for pattern in GLOB_PATTERNS:
        expected = matches(oracle.select("g").where_glob("v", pattern, case_insensitive))

        for emulate_prepare in (False, True):
            assert matches(db.select("g").where_glob("v", pattern, case_insensitive), emulate_prepare) == expected, (
                pattern
            )

        assert matches(db.select("g").where_not_glob("v", pattern, case_insensitive)) == sorted(
            compared - set(expected)
        ), pattern

    oracle.close()


def test_m5_strict_tables_accept_every_portable_type() -> None:
    db = connect_sqlite(":memory:", options={"strict": True})
    (
        db.create_table("t")
        .identity("id")
        .boolean("b")
        .integer("i")
        .float("f")
        .varchar("v")
        .text("x")
        .datetime("d")
        .execute()
    )
    db.insert("t").values({"b": True, "i": 2, "f": 1.5, "v": "v", "x": "x", "d": datetime(2024, 1, 2)}).execute()

    assert db.select("t").execute().fetch_dict() == {
        "id": 1,
        "b": 1,
        "i": 2,
        "f": 1.5,
        "v": "v",
        "x": "x",
        "d": "2024-01-02 00:00:00",
    }
    db.close()


def test_m6_pgsql_keys_for_a_read_only_role(db: Database) -> None:
    only(db, Driver.PGSQL)
    db.exec("DROP ROLE IF EXISTS sentiencedb_reader")
    db.exec("CREATE ROLE sentiencedb_reader LOGIN")

    try:
        db.exec("CREATE TABLE p (id int PRIMARY KEY, code text UNIQUE)")
        db.exec("GRANT USAGE ON SCHEMA public TO sentiencedb_reader")
        db.exec("GRANT SELECT ON p TO sentiencedb_reader")

        with _connect(Driver.PGSQL, username="sentiencedb_reader") as reader:
            assert reader.information_schema_primary_keys("p") == ["id"]
            assert [unique.columns for unique in reader.information_schema_unique_constraints("p")] == [["code"]]
    finally:
        db.exec("DROP OWNED BY sentiencedb_reader")
        db.exec("DROP ROLE sentiencedb_reader")


def test_m7_mysql_unique_key_named_like_a_foreign_key(db: Database) -> None:
    only(db, Driver.MYSQL, Driver.MARIADB)
    db.exec("CREATE TABLE parent (id INT PRIMARY KEY)")
    db.exec(
        "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT, UNIQUE KEY fk_parent (parent_id),"
        " CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES parent (id))"
    )
    (foreign_key,) = db.information_schema_foreign_key_constraints("child")

    assert (foreign_key.columns, foreign_key.reference_table, foreign_key.reference_columns) == (
        ["parent_id"],
        "parent",
        ["id"],
    )


def test_m8_mysql_defaults_round_trip(db: Database) -> None:
    only(db, Driver.MYSQL, Driver.MARIADB)
    db.exec(
        "CREATE TABLE d (tag VARCHAR(20) DEFAULT (concat('a', 'b')), flags BIT(1) DEFAULT b'1',"
        " h VARBINARY(4) DEFAULT 0x41, j JSON DEFAULT ('{}'), s VARCHAR(10) DEFAULT '0x41')"
    )
    db.exec("INSERT INTO d () VALUES ()")
    expected = db.query("SELECT tag, flags, h, j, s FROM d").fetch_dict()
    query = db.create_table("d2")

    for column in db.information_schema_columns("d"):
        query.column(column.name, column.type, column.not_null, column.default)

    query.execute()
    db.exec("INSERT INTO d2 () VALUES ()")

    assert db.query("SELECT tag, flags, h, j, s FROM d2").fetch_dict() == expected


def test_l1_sqlite_case_sensitive_like_option() -> None:
    db = connect_sqlite(":memory:", options={"deprecated_case_sensitive_like": True})
    db.create_table("t").integer("id").varchar("v").execute()
    db.insert("t").values({"id": 1, "v": "Alice"}).execute()

    assert ids(db.select("t").where_starts_with("v", "ali", True)) == [1]
    assert ids(db.select("t").where_starts_with("v", "ali")) == []
    db.close()


def test_l2_mysql_case_insensitive_like_ignores_the_collation(db: Database) -> None:
    only(db, Driver.MYSQL, Driver.MARIADB)
    db.exec("CREATE TABLE t (id INT, v VARCHAR(10) COLLATE utf8mb4_bin, b VARBINARY(10))")
    db.insert("t").values({"id": 1, "v": "Alice", "b": b"Alice"}).execute()

    for column in ("v", "b"):
        assert ids(db.select("t").where_starts_with(column, "ali", True)) == [1]
        assert ids(db.select("t").where_starts_with(column, "ali")) == []


@pytest.mark.parametrize(
    ("pattern", "flags", "expected"),
    [
        ("line1.line2", "", []),
        ("line1.line2", "s", [1]),
        ("^line2$", "", []),
        ("^line2$", "m", [1]),
        ("^LINE1.line2$", "is", [1]),
        ("^line1.line2$", "ms", [1]),
    ],
)
def test_l3_regex_flags_are_portable(db: Database, pattern: str, flags: str, expected: list[int]) -> None:
    db.create_table("t").integer("id").varchar("v").execute()
    db.insert("t").values({"id": 1, "v": "line1\nline2"}).execute()

    for emulate_prepare in (False, True):
        assert ids(db.select("t").where_regex("v", pattern, flags), emulate_prepare) == expected


def test_l4_index_in_another_schema(db: Database) -> None:
    only(db, Driver.PGSQL, Driver.SQLITE)
    schema = "main" if db.dialect.driver is Driver.SQLITE else "other"

    if schema == "other":
        db.exec("CREATE SCHEMA other")

    db.create_table([schema, "t"]).integer("a").execute()
    db.create_index([schema, "t"], "t_a").columns(["a"]).execute()
    db.drop_index([schema, "t"], "t_a").execute()
    db.create_index([schema, "t"], "t_a").columns(["a"]).execute()


def test_l7_sqlite_identity_whatever_the_table_name_case() -> None:
    db = connect_sqlite(":memory:")
    db.create_table("users").identity("id").varchar("name").execute()

    assert [column.generated_by_default_as_identity for column in db.information_schema_columns("USERS")] == [
        True,
        False,
    ]
    db.close()


def test_l8_pgsql_generated_columns_have_no_default(db: Database) -> None:
    only(db, Driver.PGSQL)
    db.exec("CREATE TABLE g (a int, b int GENERATED ALWAYS AS (a * 2) STORED)")

    assert [column.default for column in db.information_schema_columns("g")] == [None, None]


def test_l9_mysql_types(db: Database) -> None:
    only(db, Driver.MYSQL, Driver.MARIADB)
    db.create_table("t").boolean("flag").execute()
    db.exec("ALTER TABLE t ADD COLUMN n TINYINT, ADD COLUMN w TINYINT(4), ADD COLUMN ts TIMESTAMP NULL")
    types = {column.name: column.type for column in db.information_schema_columns("t")}

    assert types["flag"] == Type(TypeEnum.BOOLEAN)
    assert isinstance(types["n"], str) and types["n"].startswith("tinyint")
    assert isinstance(types["w"], str) and types["w"].startswith("tinyint")
    assert types["ts"] == "timestamp"


def test_l10_pgsql_serials_and_precision(db: Database) -> None:
    only(db, Driver.PGSQL)
    db.exec("CREATE SEQUENCE shared_seq")
    db.exec(
        "CREATE TABLE s (s smallint GENERATED BY DEFAULT AS IDENTITY, ticket bigint DEFAULT nextval('shared_seq'),"
        " t timestamp(3))"
    )
    columns = db.information_schema_columns("s")
    query = db.create_table("s2")

    for column in columns:
        query.column(column.name, column.type, column.not_null, column.default, column.generated_by_default_as_identity)

    query.execute()
    copied = db.query(
        "SELECT column_name, data_type, column_default, datetime_precision FROM information_schema.columns"
        " WHERE table_name = 's2' ORDER BY ordinal_position"
    ).fetch_dicts()

    assert [column.generated_by_default_as_identity for column in columns] == [True, False, False]
    assert [(row["data_type"], row["datetime_precision"]) for row in copied] == [
        ("smallint", None),
        ("bigint", None),
        ("timestamp without time zone", 3),
    ]
    assert copied[0]["column_default"] == "nextval('s2_s_seq'::regclass)"
    assert copied[1]["column_default"] == "nextval('shared_seq'::regclass)"
