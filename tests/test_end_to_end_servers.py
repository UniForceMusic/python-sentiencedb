"""End to end: the public :class:`Database` API against live PostgreSQL, MySQL and MariaDB servers.

Each backend is skipped when its port is unreachable (``docker compose up -d`` starts all three). The suite works in
a dedicated ``sentiencedb_e2e`` database, which every test resets before creating its tables.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from sentiencedb import (
    ConnectionLimitError,
    Database,
    Driver,
    ForeignKeyViolationError,
    Join,
    NotNullViolationError,
    QueryError,
    ReferentialActionEnum,
    SelectQuery,
    UniqueViolationError,
    WhereGroup,
    alias,
    current_timestamp,
    identifier,
    raw,
)
from sentiencedb.options import DebugCallback, Options
from sentiencedb.queries.enums import TypeEnum
from sentiencedb.queries.objects.ddl import Column, ForeignKeyConstraint, Index, Type, UniqueConstraint

pytestmark = pytest.mark.integration

DATABASE = "sentiencedb_e2e"

USERS: list[dict[str, Any]] = [
    {"name": "Alice", "email": "alice@example.com", "age": 30, "active": True},
    {"name": "bob", "email": "bob@example.com", "age": 25, "active": False},
    {"name": "Carol", "email": "carol_c@example.org", "age": None, "active": True},
    {"name": "", "email": "nobody@example.org", "age": 0, "active": False},
]


@dataclass(frozen=True)
class Backend:
    driver: Driver
    port: int
    admin_database: str
    username: str
    driver_module: str

    def connect(
        self, name: str = DATABASE, options: Options | None = None, debug: DebugCallback | None = None
    ) -> Database:
        connect = {
            Driver.PGSQL: Database.connect_postgresql,
            Driver.MYSQL: Database.connect_mysql,
            Driver.MARIADB: Database.connect_mariadb,
        }[self.driver]

        return connect(name, port=self.port, username=self.username, options=options, debug=debug)

    @property
    def mysql(self) -> bool:
        return self.driver is not Driver.PGSQL

    def reset(self, db: Database) -> None:
        """Drop everything in the test database."""
        if self.mysql:
            db.exec(f"DROP DATABASE {DATABASE}")
            db.exec(f"CREATE DATABASE {DATABASE}")
            db.exec(f"USE {DATABASE}")
        else:
            db.exec("DROP SCHEMA public CASCADE")
            db.exec("CREATE SCHEMA public")


BACKENDS = [
    Backend(Driver.PGSQL, 5432, "postgres", "postgres", "psycopg"),
    Backend(Driver.MYSQL, 3306, "mysql", "root", "mysql.connector"),
    Backend(Driver.MARIADB, 3307, "mysql", "root", "mysql.connector"),
]


def server_is_up(port: int) -> bool:
    try:
        socket.create_connection(("localhost", port), timeout=0.5).close()
    except OSError:
        return False

    return True


@pytest.fixture(scope="module", params=BACKENDS, ids=[backend.driver.value for backend in BACKENDS])
def backend(request: pytest.FixtureRequest) -> Iterator[Backend]:
    backend: Backend = request.param
    pytest.importorskip(backend.driver_module)

    if not server_is_up(backend.port):
        pytest.skip(f"{backend.driver.value} is not reachable on localhost:{backend.port}")

    with backend.connect(backend.admin_database) as admin:
        admin.exec(f"DROP DATABASE IF EXISTS {DATABASE}")
        admin.exec(f"CREATE DATABASE {DATABASE}")

    yield backend

    with backend.connect(backend.admin_database) as admin:
        admin.exec(f"DROP DATABASE {DATABASE}")


@pytest.fixture
def empty_db(backend: Backend) -> Iterator[Database]:
    with backend.connect() as db:
        backend.reset(db)
        yield db


@pytest.fixture
def db(empty_db: Database) -> Database:
    (
        empty_db.create_table("users")
        .identity("id")
        .varchar("name", not_null=True)
        .varchar("email")
        .integer("age", 32)
        .boolean("active", default=False)
        .datetime("created_at", default=current_timestamp())
        .unique_constraint(["email"])
        .execute()
    )
    (
        empty_db.create_table("posts")
        .identity("id")
        .integer("user_id", not_null=True)
        .text("title")
        .foreign_key_constraint("user_id", "users", "id")
        .execute()
    )
    empty_db.insert("users").values(*USERS).execute()
    empty_db.insert("posts").values(
        {"user_id": 1, "title": "Hello"}, {"user_id": 1, "title": "Again"}, {"user_id": 2, "title": "Bob's"}
    ).execute()

    return empty_db


def q(db: Database, *path: str) -> str:
    """An escaped identifier for raw SQL."""
    return db.dialect.escape_identifier(list(path))


def names(query: SelectQuery) -> list[str]:
    return [row["name"] for row in query.columns(["name"]).order_by_asc("id").execute()]


def default_action(db: Database) -> ReferentialActionEnum:
    """The referential action a foreign key without ``ON UPDATE``/``ON DELETE`` reports."""
    return ReferentialActionEnum.RESTRICT if db.dialect.driver is Driver.MARIADB else ReferentialActionEnum.NO_ACTION


def is_mysql(db: Database) -> bool:
    return db.dialect.driver in (Driver.MYSQL, Driver.MARIADB)


class TestCrud:
    def test_create_insert_select(self, db: Database) -> None:
        row = db.select("users").where_equals("id", 1).execute().fetch_dict()

        assert row is not None
        assert (row["name"], row["email"], row["age"]) == ("Alice", "alice@example.com", 30)
        assert db.dialect.parse_bool(row["active"]) is True
        assert isinstance(row["created_at"], datetime)

    def test_last_insert_id(self, db: Database) -> None:
        db.insert("users").values({"name": "Dave", "email": "dave@example.com"}).execute()

        assert db.last_insert_id() == 5

        if db.dialect.driver is Driver.PGSQL:
            assert db.last_insert_id("users_id_seq") == 5
            assert db.last_insert_id("posts_id_seq") == 3

    def test_update(self, db: Database) -> None:
        db.update("users").set({"age": raw(f"{q(db, 'age')} + 1"), "name": "Bob"}).where_equals("id", 2).execute()

        assert db.select("users").columns(["name", "age"]).where_equals("id", 2).execute().fetch_dict() == {
            "name": "Bob",
            "age": 26,
        }

    def test_update_with_sub_query(self, db: Database) -> None:
        posts = db.select_table("posts", "p").columns([raw("count(*)")]).where_equals(["p", "user_id"], 1)
        db.update("users").set({"age": posts}).where_equals("id", 1).execute()

        assert db.select("users").columns(["age"]).where_equals("id", 1).execute().scalar() == 2

    def test_delete(self, db: Database) -> None:
        db.delete("posts").where_equals("user_id", 1).execute()

        assert db.select("posts").count() == 1

    def test_select_shapes(self, db: Database) -> None:
        query = db.select_table("users", "u").columns({"who": ["u", "name"]}).where_greater_than(["u", "age"], 20)

        assert [row["who"] for row in query.order_by_desc("age").execute()] == ["Alice", "bob"]
        assert db.select("users").distinct().columns(["active"]).count() == 2
        assert names(db.select("users").limit(2).offset(1)) == ["bob", "Carol"]
        assert db.select_sub_query(db.select("users").where_is_not_null("age"), "adults").count() == 3

    def test_distinct_on(self, db: Database) -> None:
        query = db.select("posts").distinct(["user_id"]).columns(["user_id", "title"]).order_by_asc("user_id")

        if not db.dialect.distinct_on:
            with pytest.raises(QueryError):
                query.execute()
            return

        assert [row["user_id"] for row in query.order_by_asc("id").execute()] == [1, 2]

    def test_group_by_and_having(self, db: Database) -> None:
        rows = (
            db.select("posts")
            .columns(["user_id", alias(raw("count(*)"), "posts")])
            .group_by(["user_id"])
            .having_greater_than(raw("count(*)"), 1)
            .execute()
            .fetch_dicts()
        )

        assert rows == [{"user_id": 1, "posts": 2}]

    def test_having_variants(self, db: Database) -> None:
        def users(build: Callable[[SelectQuery], SelectQuery]) -> list[int]:
            query = db.select("posts").columns(["user_id"]).group_by(["user_id"]).order_by_asc("user_id")

            return [row["user_id"] for row in build(query).execute()]

        count = raw("count(*)")

        assert users(lambda query: query.having_equals(count, 1)) == [2]
        assert users(lambda query: query.having_between(count, 2, 3)) == [1]
        assert users(lambda query: query.having_in("user_id", [2, 3])) == [2]
        assert users(lambda query: query.having_less_than(count, 2).or_having_greater_than(count, 1)) == [1, 2]
        assert users(lambda query: query.having_group(lambda group: group.having_equals(count, 2))) == [1]
        assert users(lambda query: query.havingf("count(*) > %d", 1)) == [1]
        assert users(lambda query: query.having("count(*) = :n", {"n": 1})) == [2]

    def test_unions(self, db: Database) -> None:
        first = db.select("users").columns(["name"]).where_equals("id", 1)
        last = db.select("users").columns(["name"]).where_equals("id", 4)

        assert sorted(row["name"] for row in first.union(last).union_all(last).execute()) == ["", "", "Alice"]
        assert first.union(last).count() == 2

    def test_sub_query_column(self, db: Database) -> None:
        posts = (
            db.select("posts").columns([raw("count(*)")]).where_equals(["posts", "user_id"], identifier(["u", "id"]))
        )
        rows = db.select_table("users", "u").columns({"name": ["u", "name"], "posts": posts}).order_by_asc("id")

        assert rows.limit(2).execute().fetch_dicts() == [{"name": "Alice", "posts": 2}, {"name": "bob", "posts": 1}]

    def test_round_trips_values(self, empty_db: Database) -> None:
        db = empty_db
        db.create_table("v").identity("id").float("f").column("d", "DECIMAL(10, 2)").datetime("t").boolean("b").column(
            "blob", "BYTEA" if db.dialect.driver is Driver.PGSQL else "BLOB"
        ).execute()
        moment = datetime(2024, 1, 2, 3, 4, 5, 123456)
        values: dict[str, Any] = {"f": 1.5, "d": Decimal("12.34"), "t": moment, "b": True, "blob": b"\x00\x01'"}

        db.insert("v").values(values).execute()
        row = db.select("v").columns(list(values)).execute().fetch_dict()

        assert row is not None
        assert (row["f"], row["d"], row["t"], bytes(row["blob"])) == (1.5, Decimal("12.34"), moment, b"\x00\x01'")
        assert db.dialect.parse_bool(row["b"]) is True
        assert db.select("v").where_equals("t", moment).where_equals("b", True).count() == 1


class TestJoins:
    def test_left_and_inner_joins(self, db: Database) -> None:
        on: Callable[[Join], Join] = lambda join: join.on(["posts", "user_id"], ["users", "id"])  # noqa: E731
        left = db.select("users").columns([["users", "name"], ["posts", "title"]]).left_join("posts", on)
        inner = db.select("users").inner_join_table(
            "posts", lambda join: join.on(["p", "user_id"], ["users", "id"]), "p"
        )

        assert left.count() == 5
        assert inner.where_equals(["p", "title"], "Again").columns([["users", "name"]]).execute().scalar() == "Alice"

    def test_join_on_conditions(self, db: Database) -> None:
        query = db.select("users").inner_join(
            "posts",
            lambda join: join.on(["posts", "user_id"], ["users", "id"]).where_not_equals(["posts", "title"], "Hello"),
        )

        assert query.count() == 2
        assert query.columns([["posts", "id"], ["users", "id"]]).count() == 2

    def test_sub_query_cross_and_raw_joins(self, db: Database) -> None:
        """Joined ``SELECT *`` counts work too: ``count()`` selects ``1``, as MySQL rejects the duplicate ``id``
        columns of a derived ``SELECT *``."""

        def users() -> SelectQuery:
            return db.select("users")

        latest = db.select("posts").columns([alias(raw("max(id)"), "id")])
        posts = q(db, "posts")

        assert users().cross_join("posts").count() == 12
        assert users().cross_join_sub_query(latest, "latest").count() == 4
        assert (
            users()
            .left_join_sub_query(db.select("posts"), "p", lambda join: join.on(["p", "user_id"], ["users", "id"]))
            .where_is_null(["p", "id"])
            .count()
            == 2
        )
        assert users().join(f"INNER JOIN {posts} ON {posts}.{q(db, 'user_id')} = {q(db, 'users', 'id')}").count() == 3
        assert users().joinf(f"INNER JOIN {posts} ON {posts}.id = %d", 3).count() == 4

    def test_lateral_joins(self, db: Database) -> None:
        latest = (
            db.select("posts")
            .columns(["title"])
            .where_equals(["posts", "user_id"], identifier(["users", "id"]))
            .order_by_desc("id")
            .limit(1)
        )
        query = (
            db.select("users")
            .columns([["users", "name"], ["latest", "title"]])
            .left_join_lateral_sub_query(latest, "latest", lambda join: join.where("TRUE"))
            .order_by_asc(["users", "id"])
        )

        if not db.dialect.lateral:
            with pytest.raises(QueryError):
                query.execute()
            return

        assert [row["title"] for row in query.execute()] == ["Again", "Bob's", None, None]
        assert db.select("users").columns(["name"]).cross_join_lateral_sub_query(latest, "l").count() == 2


class TestWhere:
    @pytest.mark.parametrize(
        ("build", "expected"),
        [
            (lambda db, q: q.where_equals("name", "Alice"), ["Alice"]),
            (lambda db, q: q.where_equals("age", "30", cast=True), ["Alice"]),
            (lambda db, q: q.where_not_equals("name", "Alice"), ["bob", "Carol", ""]),
            (lambda db, q: q.where_is_null("age"), ["Carol"]),
            (lambda db, q: q.where_is_not_null("age"), ["Alice", "bob", ""]),
            (lambda db, q: q.where_like("name", "a%"), []),
            (lambda db, q: q.where_like("name", "A%"), ["Alice"]),
            (lambda db, q: q.where_like("name", "a%", case_insensitive=True), ["Alice"]),
            (lambda db, q: q.where_not_like("email", "%.com"), ["Carol", ""]),
            (lambda db, q: q.where_not_like("name", "B%", case_insensitive=True), ["Alice", "Carol", ""]),
            (lambda db, q: q.where_starts_with("name", "C"), ["Carol"]),
            (lambda db, q: q.where_starts_with("name", "c"), []),
            (lambda db, q: q.where_ends_with("email", "c@example.org"), ["Carol"]),
            (lambda db, q: q.where_contains("email", "_c@"), ["Carol"]),
            (lambda db, q: q.where_contains("email", "%"), []),
            (lambda db, q: q.where_contains("name", "ALI", case_insensitive=True), ["Alice"]),
            (lambda db, q: q.where_not_contains("email", "example.com"), ["Carol", ""]),
            (lambda db, q: q.where_glob("name", "B*"), []),
            (lambda db, q: q.where_glob("name", "b?b"), ["bob"]),
            (lambda db, q: q.where_glob("name", "B*", case_insensitive=True), ["bob"]),
            (lambda db, q: q.where_not_glob("name", "*o*"), ["Alice", ""]),
            (lambda db, q: q.where_in("age", [25, 30]), ["Alice", "bob"]),
            (lambda db, q: q.where_in("age", []), []),
            (lambda db, q: q.where_not_in("age", [25, 30]), [""]),
            (lambda db, q: q.where_not_in("age", []), ["Alice", "bob", "Carol", ""]),
            (lambda db, q: q.where_less_than("age", 25), [""]),
            (lambda db, q: q.where_less_than_or_equals("age", 25), ["bob", ""]),
            (lambda db, q: q.where_greater_than("age", 25), ["Alice"]),
            (lambda db, q: q.where_greater_than_or_equals("age", 25), ["Alice", "bob"]),
            (lambda db, q: q.where_between("age", 1, 25), ["bob"]),
            (lambda db, q: q.where_not_between("age", 1, 25), ["Alice", ""]),
            (lambda db, q: q.where_empty("age"), ["Carol", ""]),
            (lambda db, q: q.where_not_empty("age"), ["Alice", "bob"]),
            (lambda db, q: q.where_regex("email", r"^[a-c].*\.com$"), ["Alice", "bob"]),
            (lambda db, q: q.where_regex("name", "^B"), []),
            (lambda db, q: q.where_regex("name", "^B", "i"), ["bob"]),
            (lambda db, q: q.where_not_regex("name", "o"), ["Alice", ""]),
            (lambda db, q: q.where_operator("age", "<>", 30), ["bob", ""]),
            (lambda db, q: q.where_operator("age", "IN", [0, 25]), ["bob", ""]),
            (lambda db, q: q.wheref(f"{q_(db, 'age')} > %d AND {q_(db, 'name')} <> %s", 20, "bob"), ["Alice"]),
            (
                lambda db, q: q.where(
                    f"{q_(db, 'age')} = :age OR {q_(db, 'name')} = :name", {"age": 25, "name": "Carol"}
                ),
                ["bob", "Carol"],
            ),
            (lambda db, q: q.where(f"{q_(db, 'age')} = ?", [30]), ["Alice"]),
            (lambda db, q: q.where_equals("active", True).or_where_equals("age", 0), ["Alice", "Carol", ""]),
            (lambda db, q: q.where_is_null("age").or_where_like("name", "b%"), ["bob", "Carol"]),
            (lambda db, q: q.where_equals("name", "x").or_where_glob("name", "C*"), ["Carol"]),
            (lambda db, q: q.where_equals("name", "x").or_where_not_glob("name", "*"), []),
            (lambda db, q: q.where_equals("name", "x").or_where_regex("name", "^C"), ["Carol"]),
            (lambda db, q: q.where_equals("name", "x").or_where_contains("name", "ob"), ["bob"]),
        ],
    )
    def test_conditions(self, build: Callable[[Database, Any], Any], expected: list[str], db: Database) -> None:
        assert names(build(db, db.select("users"))) == expected

    def test_regex_is_case_sensitive_unless_flagged(self, db: Database) -> None:
        """MySQL >= 8 rejects a bound ``regexp_like()`` match type; MySQL and MariaDB collations ignore case."""
        for emulate_prepare in (False, True):
            query = db.select("users").columns(["name"]).order_by_asc("id")

            assert [row["name"] for row in query.where_regex("name", "^[ab]").execute(emulate_prepare)] == ["bob"]
            assert db.select("users").where_regex("name", "^[ab]", "i").count() == 2

    def test_where_empty_on_text(self, db: Database) -> None:
        assert names(db.select("users").where_empty("name")) == [""]
        assert names(db.select("users").where_not_empty("name")) == ["Alice", "bob", "Carol"]

    def test_groups(self, db: Database) -> None:
        def adults(group: WhereGroup) -> WhereGroup:
            return group.where_greater_than("age", 20).or_where_is_null("age")

        assert names(db.select("users").where_equals("active", True).where_group(adults)) == ["Alice", "Carol"]
        assert names(db.select("users").where_not_group(adults)) == [""]
        assert names(db.select("users").where_equals("name", "bob").or_where_group(adults)) == ["Alice", "bob", "Carol"]
        assert names(db.select("users").where_group(lambda group: None)) == ["Alice", "bob", "Carol", ""]

    def test_sub_queries(self, db: Database) -> None:
        authors = db.select("posts").columns(["user_id"])
        has_posts = db.select("posts").where_equals(["posts", "user_id"], identifier(["users", "id"]))

        assert names(db.select("users").where_in("id", authors)) == ["Alice", "bob"]
        assert names(db.select("users").where_not_in("id", authors)) == ["Carol", ""]
        assert names(db.select("users").where_exists(has_posts)) == ["Alice", "bob"]
        assert names(db.select("users").where_not_exists(has_posts)) == ["Carol", ""]

    def test_emulate_prepare(self, db: Database) -> None:
        query = (
            db.select("users").columns(["name"]).where_equals("email", "bob@example.com").where_equals("active", False)
        )

        assert query.execute(emulate_prepare=True).scalar() == "bob"
        assert names(db.select("users").where_contains("email", "_c@")) == ["Carol"]

    def test_strings_are_bound_and_escaped(self, db: Database) -> None:
        nasty = 'it\'s a \\ "quote" ? :name %s -- x'

        db.insert("users").values({"name": nasty, "email": "nasty@example.com"}).execute()
        db.insert("users").values({"name": nasty, "email": "nasty2@example.com"}).execute(emulate_prepare=True)

        assert db.select("users").where_equals("name", nasty).count() == 2


def q_(db: Database, column: str) -> str:
    return q(db, column)


class TestUpsertAndReturning:
    def test_insert_returning(self, db: Database) -> None:
        query = db.insert("users").values({"name": "Dave", "email": "dave@example.com"}).returning(["id", "name"])

        assert query.last_insert_id("id").execute().fetch_dicts() == [{"id": 5, "name": "Dave"}]

    @pytest.mark.parametrize("emulate", [False, True])
    def test_multi_row_insert_returning(self, emulate: bool, db: Database) -> None:
        query = db.insert("users").values({"name": "Dave", "email": "d@x"}, {"name": "Eve", "email": "e@x"})
        query.returning(["id", "name"]).last_insert_id("id")

        if emulate:
            query.emulate_returning("id")

        assert query.execute().fetch_dicts() == [{"id": 5, "name": "Dave"}, {"id": 6, "name": "Eve"}]

    def test_update_and_delete_returning(self, db: Database) -> None:
        update = db.update("users").set({"age": 31}).where_equals("id", 1).returning(["age"])
        delete = db.delete("posts").where_equals("user_id", 1).returning()

        if not db.dialect.update_returning:
            with pytest.raises(QueryError):
                update.execute()
        else:
            assert update.execute().fetch_dicts() == [{"age": 31}]

        if not db.dialect.returning:
            with pytest.raises(QueryError):
                delete.execute()
        else:
            assert sorted(row["title"] for row in delete.execute()) == ["Again", "Hello"]

    def test_emulated_returning(self, db: Database) -> None:
        query = db.insert("users").values({"name": "Dave", "email": "d@x"}).returning(["name"]).emulate_returning("id")

        assert query.execute().fetch_dicts() == [{"name": "Dave"}]

    @pytest.mark.parametrize("emulate", [False, True])
    def test_returning_an_explicit_id(self, emulate: bool, db: Database) -> None:
        """``LAST_INSERT_ID()`` / ``lastval()`` do not cover a supplied id; the row is found by its own id."""
        query = db.insert("users").values({"id": 100, "name": "Dave", "email": "d@x"}).returning(["id", "name"])
        query.last_insert_id("id")

        if emulate:
            query.emulate_returning("id")

        assert query.execute().fetch_dicts() == [{"id": 100, "name": "Dave"}]

    @pytest.mark.parametrize("emulate", [False, True])
    def test_upsert_returning_finds_the_updated_row(self, emulate: bool, db: Database) -> None:
        """The update path generates no id, so the last insert id would point at another (or no) row."""
        db.insert("users").values({"name": "Eve", "email": "eve@example.com"}).execute()
        query = db.insert("users").values({"name": "Alicia", "email": "alice@example.com"})
        query.on_conflict_do_update(["email"]).returning(["email", "name"]).last_insert_id("id")

        if emulate:
            query.emulate_returning("id")

        assert query.execute().fetch_dicts() == [{"email": "alice@example.com", "name": "Alicia"}]
        assert db.select("users").count() == 5

    @pytest.mark.parametrize("emulate", [False, True])
    def test_do_nothing_returns_only_inserted_rows(self, emulate: bool, db: Database) -> None:
        """PostgreSQL semantics everywhere: a skipped row returns nothing, an inserted row returns itself."""
        query = db.insert("users").values(
            {"name": "x", "email": "bob@example.com"}, {"name": "Eve", "email": "eve@example.com"}
        )
        query.on_conflict_do_nothing(["email"]).returning(["name"]).last_insert_id("id")

        if emulate:
            query.emulate_on_conflict("id")

        assert query.execute().fetch_dicts() == [{"name": "Eve"}]
        assert names(db.select("users").where_equals("email", "bob@example.com")) == ["bob"]

    @pytest.mark.parametrize("emulate", [False, True])
    def test_upsert_never_conflicts_on_null(self, emulate: bool, db: Database) -> None:
        db.insert("users").values({"name": "first", "email": None}).execute()
        query = db.insert("users").values({"name": "second", "email": None}).on_conflict_do_update(["email"])

        if emulate:
            query.emulate_on_conflict("id")

        query.execute()

        assert names(db.select("users").where_is_null("email")) == ["first", "second"]

    def test_emulated_upsert_is_atomic(self, db: Database) -> None:
        rows = [{"name": "Eve", "email": "eve@example.com"}, {"name": None, "email": "frank@example.com"}]
        query = db.insert("users").values(*rows).on_conflict_do_update(["email"]).emulate_on_conflict("id")

        db.begin_transaction()
        db.insert("posts").values({"user_id": 2, "title": "kept"}).execute()

        with pytest.raises(NotNullViolationError):
            query.execute()

        db.commit_transaction()

        assert db.select("users").count() == 4
        assert db.select("posts").where_equals("title", "kept").count() == 1

        with pytest.raises(QueryError, match="constraint columns"):
            db.insert("users").values(rows[0], {"name": "Gus"}).on_conflict_do_nothing(["email"]).emulate_on_conflict(
                "id"
            ).execute()

        assert db.select("users").count() == 4

    def test_native_upsert(self, db: Database) -> None:
        row = {"name": "Alicia", "email": "alice@example.com"}
        age = q(db, "users", "age")

        db.insert("users").values(row).on_conflict_do_nothing(["email"]).execute()
        assert names(db.select("users").where_equals("id", 1)) == ["Alice"]

        updated = (
            db.insert("users")
            .values(row)
            .on_conflict_do_update(["email"])
            .returning(["id", "name"])
            .last_insert_id("id")
            .execute()
        )
        assert updated.fetch_dicts() == [{"id": 1, "name": "Alicia"}]

        db.insert("users").values(row).on_conflict_do_update(["email"], {"age": raw(f"{age} + 1")}).execute()
        assert db.select("users").columns(["age"]).where_equals("id", 1).execute().scalar() == 31
        assert db.select("users").count() == 4

    def test_native_upsert_inserts_new_rows(self, db: Database) -> None:
        query = (
            db.insert("users")
            .values({"name": "Eve", "email": "eve@example.com"})
            .on_conflict_do_update(["email"])
            .returning(["name"])
            .last_insert_id("id")
        )

        assert [row["name"] for row in query.execute()] == ["Eve"]
        assert db.select("users").count() == 5

    def test_named_constraint_upsert(self, db: Database) -> None:
        db.create_table("tags").identity("id").varchar("label").unique_constraint(["label"], "tags_label").execute()
        db.insert("tags").values({"label": "a"}).execute()
        query = db.insert("tags").values({"label": "a"}).on_conflict_do_nothing("tags_label")

        query.execute()

        assert db.select("tags").count() == 1

        with pytest.raises(QueryError, match="not the constraint name"):
            query.emulate_on_conflict("id").execute()

    def test_emulated_upsert(self, db: Database) -> None:
        query = (
            db.insert("users")
            .values({"name": "Alicia", "email": "alice@example.com"}, {"name": "Eve", "email": "eve@example.com"})
            .on_conflict_do_update(["email"])
            .returning(["name"])
            .emulate_on_conflict("id", in_transaction=True)
        )

        assert [row["name"] for row in query.execute()] == ["Alicia", "Eve"]
        assert names(db.select("users").where_in("id", [1, 5])) == ["Alicia", "Eve"]

    def test_emulated_do_nothing(self, db: Database) -> None:
        db.insert("users").values({"name": "x", "email": "bob@example.com"}).on_conflict_do_nothing(
            ["email"]
        ).emulate_on_conflict("id").execute()

        assert names(db.select("users").where_equals("email", "bob@example.com")) == ["bob"]


class TestErrors:
    def test_unique_violation(self, db: Database) -> None:
        with pytest.raises(UniqueViolationError) as error:
            db.insert("users").values({"name": "x", "email": "bob@example.com"}).execute()

        assert error.value.sqlstate in ("23505", "23000")

    def test_foreign_key_violation(self, db: Database) -> None:
        with pytest.raises(ForeignKeyViolationError):
            db.insert("posts").values({"user_id": 99, "title": "orphan"}).execute()

        with pytest.raises(ForeignKeyViolationError):
            db.delete("users").where_equals("id", 1).execute()

    def test_not_null_violation(self, db: Database) -> None:
        with pytest.raises(NotNullViolationError):
            db.insert("users").values({"name": None, "email": "null@example.com"}).execute()

    def test_debug_callback(self, backend: Backend, db: Database) -> None:
        statements: list[tuple[str, str | None]] = []

        with backend.connect(debug=lambda query, start, error: statements.append((query, error))) as debugged:
            debugged.select("users").where_equals("name", "it's").execute()

            with pytest.raises(UniqueViolationError):
                debugged.insert("users").values({"name": "x", "email": "bob@example.com"}).execute()

        [select, insert] = statements[-2:]
        literal = db.dialect.cast_to_query("it's")

        assert select == (f"SELECT * FROM {q(db, 'users')} WHERE {q(db, 'name')} = {literal}", None)
        assert q(db, "email") in insert[0]
        assert insert[1] is not None and "bob@example.com" in insert[1]


class TestTransactions:
    def test_commit_and_rollback(self, db: Database) -> None:
        db.begin_transaction()
        db.update("users").set({"name": "A"}).where_equals("id", 1).execute()
        assert db.in_transaction()
        db.rollback_transaction()
        assert not db.in_transaction()

        db.begin_transaction()
        db.update("users").set({"name": "B"}).where_equals("id", 2).execute()
        db.commit_transaction()

        assert names(db.select("users").where_in("id", [1, 2])) == ["Alice", "B"]

    def test_transaction_callback_and_savepoints(self, db: Database) -> None:
        def inner(db: Database) -> None:
            db.delete("posts").execute()
            raise RuntimeError("undo")

        def outer(db: Database) -> int:
            db.insert("posts").values({"user_id": 3, "title": "Carol's"}).execute()

            with pytest.raises(RuntimeError):
                db.transaction(inner)

            db.transaction(lambda db: db.insert("posts").values({"user_id": 4, "title": "Kept"}).execute())

            return db.select("posts").count()

        assert db.transaction(outer) == 5
        assert not db.in_transaction()
        assert db.select("posts").count() == 5

    def test_named_savepoints(self, db: Database) -> None:
        db.begin_transaction()
        db.begin_transaction("first")
        db.delete("posts").execute()
        db.rollback_transaction()
        db.commit_transaction()

        assert db.select("posts").count() == 3

    def test_failed_transaction_rolls_back(self, db: Database) -> None:
        def fail(db: Database) -> None:
            db.delete("posts").execute()
            db.insert("users").values({"name": "x", "email": "bob@example.com"}).execute()

        with pytest.raises(UniqueViolationError):
            db.transaction(fail)

        assert not db.in_transaction()
        assert db.select("posts").count() == 3

    def test_failed_savepoint_keeps_the_transaction_usable(self, db: Database) -> None:
        def duplicate(db: Database) -> None:
            db.insert("users").values({"name": "x", "email": "bob@example.com"}).execute()

        def outer(db: Database) -> int:
            db.delete("posts").where_equals("user_id", 2).execute()

            with pytest.raises(UniqueViolationError):
                db.transaction(duplicate)

            return db.select("posts").count()

        assert db.transaction(outer) == 2


class TestDdl:
    def test_create_if_not_exists_and_drop_if_exists(self, db: Database) -> None:
        db.create_table("users").if_not_exists().integer("x").execute()
        db.create_table("users").if_not_exists().emulate_if_not_exists().integer("x").execute()
        db.drop_table("missing").if_exists().execute()
        db.drop_table("missing").if_exists().emulate_if_exists().execute()
        db.drop_table("posts").execute()

        assert db.select("users").count() == 4
        assert db.information_schema_tables() == ["users"]

    def test_emulated_exists_checks_honour_the_schema(self, db: Database) -> None:
        """``["schema", "table"]`` is looked up in that schema (a database on MySQL), not the current one."""
        other = "sentiencedb_e2e_other"
        drop = f"DROP DATABASE IF EXISTS {other}" if is_mysql(db) else f"DROP SCHEMA IF EXISTS {other} CASCADE"
        db.exec(drop)
        db.exec(f"CREATE DATABASE {other}" if is_mysql(db) else f"CREATE SCHEMA {other}")

        def exists(table: str) -> bool:
            sql = "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?"
            return bool(db.prepared(sql, [other, table]).scalar())

        try:
            for _ in range(2):
                db.create_table([other, "users"]).if_not_exists().emulate_if_not_exists().integer("x").execute()
                db.create_table(identifier([other, "only"])).if_not_exists().emulate_if_not_exists().integer(
                    "x"
                ).execute()

            assert exists("users") and exists("only")

            for _ in range(2):
                db.create_index([other, "users"], "by_x").if_not_exists().emulate_if_not_exists().columns("x").execute()

            assert "by_x" not in [index.name for index in db.information_schema_indexes("users")]

            for _ in range(2):
                db.drop_index([other, "users"], "by_x").if_exists().emulate_if_exists().execute()
                db.drop_table([other, "only"]).if_exists().emulate_if_exists().execute()

            assert exists("users") and not exists("only")
            assert db.select("users").count() == 4
        finally:
            db.exec(drop)

    def test_emulated_exists_checks_unwrap_identifiers(self, db: Database) -> None:
        db.create_table(identifier("users")).if_not_exists().emulate_if_not_exists().integer("x").execute()
        db.drop_table(identifier("missing")).if_exists().emulate_if_exists().execute()

        assert db.table(identifier("users")).primary_keys() == ["id"]

    def test_column_helpers_and_constraints(self, empty_db: Database) -> None:
        db = empty_db
        moment = datetime(2024, 1, 2, 3, 4, 5)
        db.create_table("parents").auto_increment("id", 32).varchar("code", 10, not_null=True).unique_constraint(
            ["code"], "parents_code"
        ).execute()
        (
            db.create_table("everything")
            .integer("a", 32, not_null=True)
            .integer("b", 16, not_null=True)
            .boolean("flag", not_null=True, default=True)
            .integer("big", default=-1)
            .float("small_float", 32, default=1.5)
            .float("double", default=Decimal("2.25"))
            .varchar("label", 20, default="it's")
            .text("body")
            .text("long_body", 70000)
            .datetime("at", default=moment)
            .datetime("seconds", 0, default=current_timestamp())
            .column("parent", "INTEGER", default=raw("NULL"))
            .primary_keys(["a", "b"])
            .unique_constraint(["label", "big"])
            .foreign_key_constraint(
                "parent", "parents", "id", "everything_parent", on_delete=ReferentialActionEnum.SET_NULL
            )
            .constraint(f"CHECK ({q(db, 'big')} < 100)")
            .execute()
        )
        db.insert("parents").values({"code": "p"}).execute()
        db.insert("everything").values({"a": 1, "b": 2, "parent": 1}).execute()
        db.delete("parents").execute()
        row = db.select("everything").execute().fetch_dict()

        assert row is not None
        assert db.dialect.parse_bool(row["flag"]) is True
        assert (row["big"], row["small_float"], row["label"], row["at"], row["parent"]) == (
            -1,
            1.5,
            "it's",
            moment,
            None,
        )
        assert float(row["double"]) == 2.25
        assert isinstance(row["seconds"], datetime)
        assert db.information_schema_primary_keys("everything") == ["a", "b"]
        assert [unique.columns for unique in db.information_schema_unique_constraints("everything")] == [
            ["label", "big"]
        ]

    def test_check_violation(self, empty_db: Database) -> None:
        from sentiencedb import CheckViolationError

        db = empty_db
        db.create_table("checked").integer("n").constraint(f"CHECK ({q(db, 'n')} > 0)").execute()

        with pytest.raises(CheckViolationError):
            db.insert("checked").values({"n": 0}).execute()

    def test_alter_table(self, db: Database) -> None:
        modify = "TYPE BIGINT" if db.dialect.driver is Driver.PGSQL else "BIGINT"
        db.alter_table("users").add_varchar("nickname", 50, default="none").add_integer(
            "score", not_null=True, default=0
        ).rename_column("age", "years").drop_column("created_at").alter_column("years", modify).execute()

        row = db.select("users").where_equals("id", 1).execute().fetch_dict()

        assert row is not None
        assert {key: value for key, value in row.items() if key != "active"} == {
            "id": 1,
            "name": "Alice",
            "email": "alice@example.com",
            "years": 30,
            "nickname": "none",
            "score": 0,
        }
        assert db.information_schema_columns("users")[3] == Column("years", Type(TypeEnum.INTEGER, 64))

    def test_alter_every_helper(self, empty_db: Database) -> None:
        db = empty_db
        db.create_table("owners").integer("id", not_null=True).primary_keys(["id"]).execute()
        db.create_table("pets").integer("id", not_null=True).integer("owner_id").varchar("name").execute()

        db.alter_table("pets").add_primary_keys(["id"]).add_unique_constraint(["name"], "pets_name").execute()
        db.alter_table("pets").add_foreign_key_constraint(
            "owner_id", "owners", "id", "pets_owner", on_delete=ReferentialActionEnum.CASCADE
        ).execute()
        db.alter_table("pets").add_boolean("good", default=True).add_float("weight", 32).add_text("bio").add_datetime(
            "born", default=current_timestamp()
        ).add_column("tag", "VARCHAR(5)").alter(f"ADD {q(db, 'raw_col')} INTEGER").execute()

        assert db.information_schema_primary_keys("pets") == ["id"]
        assert db.information_schema_unique_constraints("pets") == [UniqueConstraint(["name"], "pets_name")]
        assert db.information_schema_foreign_key_constraints("pets") == [
            ForeignKeyConstraint(
                ["owner_id"],
                "owners",
                ["id"],
                "pets_owner",
                default_action(db),
                ReferentialActionEnum.CASCADE,
            )
        ]
        assert [column.name for column in db.information_schema_columns("pets")][3:] == [
            "good",
            "weight",
            "bio",
            "born",
            "tag",
            "raw_col",
        ]

        db.insert("owners").values({"id": 1}).execute()
        db.insert("pets").values({"id": 1, "owner_id": 1, "name": "Rex"}).execute()
        db.delete("owners").execute()
        assert db.select("pets").count() == 0

        db.alter_table("pets").drop_constraint("pets_owner").execute()
        db.alter_table("pets").drop_constraint("pets_name").execute()
        db.insert("pets").values({"id": 1, "owner_id": 9, "name": "Rex"}, {"id": 2, "name": "Rex"}).execute()
        assert db.select("pets").count() == 2

    def test_add_identity(self, empty_db: Database) -> None:
        db = empty_db
        db.create_table("counters").varchar("label").execute()
        db.insert("counters").values({"label": "a"}).execute()
        db.alter_table("counters").add_identity("id").execute()
        db.insert("counters").values({"label": "b"}).execute()

        assert db.select("counters").columns(["id", "label"]).order_by_asc("id").execute().fetch_dicts() == [
            {"id": 1, "label": "a"},
            {"id": 2, "label": "b"},
        ]
        assert db.information_schema_primary_keys("counters") == ["id"]

    def test_indexes(self, db: Database) -> None:
        db.create_index("users", "users_name").columns(["name"]).unique().execute()
        db.create_index("users", "users_name").if_not_exists().columns(["name"]).execute()
        db.create_index("users", "users_name").if_not_exists().emulate_if_not_exists().columns(["name"]).execute()

        assert Index("users_name", ["name"], True) in db.information_schema_indexes("users")

        with pytest.raises(UniqueViolationError):
            db.insert("users").values({"name": "Alice", "email": "a2@example.com"}).execute()

        db.drop_index("users", "users_name").execute()
        db.drop_index("users", "users_name").if_exists().execute()
        db.drop_index("users", "users_name").if_exists().emulate_if_exists().execute()
        db.insert("users").values({"name": "Alice", "email": "a2@example.com"}).execute()

        assert "users_name" not in [index.name for index in db.information_schema_indexes("users")]

    def test_partial_index(self, db: Database) -> None:
        query = db.create_index("users", "active_age").columns(["age"]).where_equals("active", True)

        if is_mysql(db):
            with pytest.raises(QueryError):
                query.execute()
            return

        query.execute()
        definition = db.query("SELECT indexdef FROM pg_indexes WHERE indexname = 'active_age'").scalar()

        assert definition.endswith("WHERE (active = true)")

    def test_index_names_per_table(self, db: Database) -> None:
        """MySQL names indexes per table, PostgreSQL per schema; the emulated checks follow the engine."""

        def has_by_id(table: str) -> bool:
            return "by_id" in [index.name for index in db.information_schema_indexes(table)]

        db.create_index("users", "by_id").columns(["id"]).if_not_exists().emulate_if_not_exists().execute()
        db.create_index("posts", "by_id").columns(["id"]).if_not_exists().emulate_if_not_exists().execute()

        assert (has_by_id("users"), has_by_id("posts")) == (True, is_mysql(db))

        db.drop_index("posts", "by_id").if_exists().emulate_if_exists().execute()

        assert (has_by_id("users"), has_by_id("posts")) == (is_mysql(db), False)


class TestIntrospection:
    def test_tables_and_schemas(self, db: Database) -> None:
        assert db.information_schema_tables() == ["posts", "users"]
        assert DATABASE in db.information_schema_schemas() or "public" in db.information_schema_schemas()

    def test_round_trip(self, db: Database) -> None:
        copy = db.create_table("users_copy").primary_keys(db.information_schema_primary_keys("users"))

        for column in db.information_schema_columns("users"):
            copy.column(
                column.name, column.type, column.not_null, column.default, column.generated_by_default_as_identity
            )

        for unique in db.information_schema_unique_constraints("users"):
            copy.unique_constraint(unique.columns)

        copy.execute()

        def describe(table: str) -> tuple[Any, ...]:
            return (
                db.information_schema_columns(table),
                db.information_schema_primary_keys(table),
                [unique.columns for unique in db.information_schema_unique_constraints(table)],
            )

        assert describe("users_copy") == describe("users")
        assert db.information_schema_foreign_key_constraints("posts") == [
            ForeignKeyConstraint(
                ["user_id"],
                "users",
                ["id"],
                db.information_schema_foreign_key_constraints("posts")[0].name,
                default_action(db),
                default_action(db),
            )
        ]

    def test_defaults(self, empty_db: Database) -> None:
        """No default, an explicit ``DEFAULT NULL`` and the string ``'NULL'`` are told apart (MariaDB reports SQL)."""
        db = empty_db
        db.create_table("defaults").varchar("none").varchar("null", default=raw("NULL")).varchar(
            "text", default="NULL"
        ).integer("zero", 32, default=0).execute()

        expected = {
            Driver.PGSQL: [raw("'NULL'::character varying"), raw("0")],
            Driver.MYSQL: [raw("'NULL'"), raw("'0'")],
            Driver.MARIADB: [raw("'NULL'"), raw("0")],
        }[db.dialect.driver]

        assert [column.default for column in db.information_schema_columns("defaults")] == [None, None, *expected]

        db.insert("defaults").values({"none": None}).execute()

        assert db.select("defaults").columns(["null", "text", "zero"]).execute().fetch_dict() == {
            "null": None,
            "text": "NULL",
            "zero": 0,
        }


class TestQueryFactory:
    def test_table(self, empty_db: Database) -> None:
        db = empty_db
        tags = db.table("tags")
        tags.create().identity("id").varchar("label", not_null=True).execute()
        tags.create_index("tags_label").columns(["label"]).execute()
        tags.insert({"label": "a"}, {"label": "b"}, {"label": "c"}).execute()
        tags.update({"label": "B"}).where_equals("label", "b").execute()
        tags.delete().where_equals("label", "c").execute()
        tags.alter().add_boolean("hidden", default=False).execute()

        rows = tags.select(["label", "hidden"]).order_by_asc("id").execute().fetch_dicts()

        assert [(row["label"], db.dialect.parse_bool(row["hidden"])) for row in rows] == [("a", False), ("B", False)]
        assert [column.name for column in tags.columns()] == ["id", "label", "hidden"]
        assert tags.primary_keys() == ["id"]
        assert tags.unique_constraints() == []
        assert tags.foreign_key_constraints() == []
        assert tags.indexes() == [Index("tags_label", ["label"], False)]

        tags.drop_index("tags_label").execute()
        tags.drop().execute()

        assert db.information_schema_tables() == []


class TestThreads:
    def test_threads_use_their_own_connections(self, db: Database) -> None:
        connection_id = "pg_backend_pid()" if db.dialect.driver is Driver.PGSQL else "CONNECTION_ID()"
        ids: list[int] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def work(number: int) -> None:
            try:
                db.transaction(
                    lambda db: db.insert("posts").values({"user_id": 1, "title": f"thread {number}"}).execute()
                )
                with lock:
                    ids.append(db.query(f"SELECT {connection_id} AS id").scalar())
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=work, args=(number,)) for number in range(4)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert errors == []
        assert len(set(ids)) == 4
        assert db.query(f"SELECT {connection_id} AS id").scalar() not in ids
        assert db.select("posts").where_starts_with("title", "thread").count() == 4

    def test_transactions_are_per_thread(self, db: Database) -> None:
        db.begin_transaction()
        db.delete("posts").execute()
        seen: list[int] = []

        thread = threading.Thread(target=lambda: seen.append(db.select("posts").count()))
        thread.start()
        thread.join()
        db.rollback_transaction()

        assert seen == [3]

    def test_connection_limit(self, backend: Backend) -> None:
        with backend.connect(options={"max_connections": 1, "acquire_timeout": 0.05}) as db:
            db.query("SELECT 1")  # the main thread now holds the only slot
            errors: list[BaseException] = []

            def work() -> None:
                try:
                    db.query("SELECT 1")
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=work)
            thread.start()
            thread.join()

            assert [type(error) for error in errors] == [ConnectionLimitError]
