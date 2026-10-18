"""End to end: the public :class:`Database` API against a real in-memory SQLite database."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest

from sentiencedb import (
    Database,
    Driver,
    Join,
    QueryError,
    SelectQuery,
    UniqueViolationError,
    WhereGroup,
    alias,
    current_timestamp,
    identifier,
    raw,
)

USERS: list[dict[str, Any]] = [
    {"name": "Alice", "email": "alice@example.com", "age": 30, "active": True},
    {"name": "bob", "email": "bob@example.com", "age": 25, "active": False},
    {"name": "Carol", "email": "carol_c@example.org", "age": None, "active": True},
    {"name": "", "email": "nobody@example.org", "age": 0, "active": False},
]


@pytest.fixture
def db() -> Iterator[Database]:
    with Database.connect_sqlite() as db:
        db.create_table("users").identity("id").varchar("name", not_null=True).varchar("email").integer(
            "age", 32
        ).boolean("active", default=False).datetime("created_at", default=current_timestamp()).unique_constraint(
            ["email"]
        ).execute()
        db.create_table("posts").identity("id").integer("user_id", not_null=True).text("title").foreign_key_constraint(
            "user_id", "users", "id"
        ).execute()

        db.insert("users").values(*USERS).execute()
        db.insert("posts").values(
            {"user_id": 1, "title": "Hello"}, {"user_id": 1, "title": "Again"}, {"user_id": 2, "title": "Bob's"}
        ).execute()

        yield db


def names(query: SelectQuery) -> list[str]:
    return [row["name"] for row in query.columns(["name"]).order_by_asc("id").execute()]


class TestCrud:
    def test_create_insert_select(self, db: Database) -> None:
        row = db.select("users").where_equals("id", 1).execute().fetch_dict()

        assert row is not None
        assert (row["name"], row["email"], row["age"], row["active"]) == ("Alice", "alice@example.com", 30, 1)
        assert isinstance(db.dialect.parse_datetime(row["created_at"]), datetime)
        assert db.dialect.driver is Driver.SQLITE

    def test_last_insert_id(self, db: Database) -> None:
        db.insert("users").values({"name": "Dave", "email": "dave@example.com"}).execute()

        assert db.last_insert_id() == 5

    def test_update(self, db: Database) -> None:
        db.update("users").set({"age": raw('"age" + 1'), "name": "Bob"}).where_equals("id", 2).execute()

        assert db.select("users").columns(["name", "age"]).where_equals("id", 2).execute().fetch_dict() == {
            "name": "Bob",
            "age": 26,
        }

    def test_delete(self, db: Database) -> None:
        db.delete("posts").where_equals("user_id", 1).execute()

        assert db.select("posts").count() == 1

    def test_select_shapes(self, db: Database) -> None:
        query = db.select_table("users", "u").columns({"who": ["u", "name"]}).where_greater_than(["u", "age"], 20)

        assert [row["who"] for row in query.order_by_desc("age").execute()] == ["Alice", "bob"]
        assert db.select("users").distinct().columns(["active"]).count() == 2
        assert names(db.select("users").limit(2).offset(1)) == ["bob", "Carol"]
        assert db.select_sub_query(db.select("users").where_is_not_null("age"), "adults").count() == 3

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

    def test_unions(self, db: Database) -> None:
        first = db.select("users").columns(["name"]).where_equals("id", 1)
        last = db.select("users").columns(["name"]).order_by_desc("id").limit(1)

        assert sorted(row["name"] for row in first.union(last).union_all(last).execute()) == ["", "", "Alice"]
        assert first.union(last).count() == 2

    def test_sub_query_column(self, db: Database) -> None:
        posts = (
            db.select("posts").columns([raw("count(*)")]).where_equals(["posts", "user_id"], identifier(["u", "id"]))
        )
        rows = db.select_table("users", "u").columns({"name": ["u", "name"], "posts": posts}).limit(2).execute()

        assert rows.fetch_dicts() == [{"name": "Alice", "posts": 2}, {"name": "bob", "posts": 1}]

    def test_multi_row_insert_needs_the_same_columns(self, db: Database) -> None:
        with pytest.raises(QueryError, match="DEFAULT in VALUES"):
            db.insert("users").values({"name": "x", "email": "x"}, {"name": "y"}).execute()


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

    def test_sub_query_cross_and_raw_joins(self, db: Database) -> None:
        latest = db.select("posts").columns([alias(raw("max(id)"), "id")])

        assert db.select("users").cross_join("posts").count() == 12
        assert db.select("users").cross_join_sub_query(latest, "latest").count() == 4
        assert (
            db.select("users")
            .left_join_sub_query(db.select("posts"), "p", lambda join: join.on(["p", "user_id"], ["users", "id"]))
            .where_is_null(["p", "id"])
            .count()
            == 2
        )
        assert db.select("users").join('INNER JOIN "posts" ON "posts"."user_id" = "users"."id"').count() == 3
        assert db.select("users").joinf('INNER JOIN "posts" ON "posts"."id" = %d', 3).count() == 4


class TestWhere:
    @pytest.mark.parametrize(
        ("build", "expected"),
        [
            (lambda q: q.where_equals("name", "Alice"), ["Alice"]),
            (lambda q: q.where_equals("age", "30", cast=True), ["Alice"]),
            (lambda q: q.where_not_equals("name", "Alice"), ["bob", "Carol", ""]),
            (lambda q: q.where_is_null("age"), ["Carol"]),
            (lambda q: q.where_is_not_null("age"), ["Alice", "bob", ""]),
            (lambda q: q.where_like("name", "a%"), []),
            (lambda q: q.where_like("name", "a%", case_insensitive=True), ["Alice"]),
            (lambda q: q.where_not_like("email", "%.com"), ["Carol", ""]),
            (lambda q: q.where_starts_with("name", "C"), ["Carol"]),
            (lambda q: q.where_ends_with("email", "c@example.org"), ["Carol"]),
            (lambda q: q.where_contains("email", "_c@"), ["Carol"]),
            (lambda q: q.where_contains("name", "ALI", case_insensitive=True), ["Alice"]),
            (lambda q: q.where_not_contains("email", "example.com"), ["Carol", ""]),
            (lambda q: q.where_glob("name", "[AB]*"), ["Alice"]),
            (lambda q: q.where_glob("name", "b*", case_insensitive=True), ["bob"]),
            (lambda q: q.where_not_glob("name", "*o*"), ["Alice", ""]),
            (lambda q: q.where_in("age", [25, 30]), ["Alice", "bob"]),
            (lambda q: q.where_in("age", []), []),
            (lambda q: q.where_not_in("age", [25, 30]), [""]),
            (lambda q: q.where_less_than("age", 25), [""]),
            (lambda q: q.where_less_than_or_equals("age", 25), ["bob", ""]),
            (lambda q: q.where_greater_than("age", 25), ["Alice"]),
            (lambda q: q.where_greater_than_or_equals("age", 25), ["Alice", "bob"]),
            (lambda q: q.where_between("age", 1, 25), ["bob"]),
            (lambda q: q.where_not_between("age", 1, 25), ["Alice", ""]),
            (lambda q: q.where_empty("age"), ["Carol", ""]),
            (lambda q: q.where_empty("name"), [""]),
            (lambda q: q.where_not_empty("age"), ["Alice", "bob"]),
            (lambda q: q.where_regex("email", r"^[a-c].*\.com$"), ["Alice", "bob"]),
            (lambda q: q.where_regex("name", "^b", "i"), ["bob"]),
            (lambda q: q.where_not_regex("name", "o"), ["Alice", ""]),
            (lambda q: q.where_operator("age", "<>", 30), ["bob", ""]),
            (lambda q: q.where_operator("age", "IN", [0, 25]), ["bob", ""]),
            (lambda q: q.wheref('"age" > %d AND "name" <> %s', 20, "bob"), ["Alice"]),
            (lambda q: q.where('"age" = :age OR "name" = :name', {"age": 25, "name": "Carol"}), ["bob", "Carol"]),
            (lambda q: q.where('"age" = ?', [30]), ["Alice"]),
            (lambda q: q.where_equals("active", True).or_where_equals("age", 0), ["Alice", "Carol", ""]),
            (lambda q: q.where_is_null("age").or_where_like("name", "b%"), ["bob", "Carol"]),
            (lambda q: q.where_equals("name", "x").or_where_glob("name", "C*"), ["Carol"]),
            (lambda q: q.where_equals("name", "x").or_where_not_glob("name", "*"), []),
        ],
    )
    def test_conditions(self, build: Callable[[Any], Any], expected: list[str], db: Database) -> None:
        assert names(build(db.select("users"))) == expected

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
        query = db.select("users").columns(["name"]).where_equals("email", "bob@example.com").where_equals("active", 0)

        assert query.execute(emulate_prepare=True).scalar() == "bob"


class TestUpsertAndReturning:
    def test_insert_returning(self, db: Database) -> None:
        result = db.insert("users").values({"name": "Dave", "email": "dave@example.com"}).returning(["id", "name"])

        assert result.execute().fetch_dicts() == [{"id": 5, "name": "Dave"}]

    def test_update_and_delete_returning(self, db: Database) -> None:
        updated = db.update("users").set({"age": 31}).where_equals("id", 1).returning(["age"]).execute()
        deleted = db.delete("posts").where_equals("user_id", 1).returning().execute()

        assert updated.fetch_dicts() == [{"age": 31}]
        assert [row["title"] for row in deleted] == ["Hello", "Again"]

    def test_emulated_returning(self, db: Database) -> None:
        query = db.insert("users").values({"name": "Dave", "email": "d@x"}).returning(["name"]).emulate_returning("id")

        assert query.execute().fetch_dicts() == [{"id": 5, "name": "Dave"}]

    def test_native_upsert(self, db: Database) -> None:
        row = {"name": "Alicia", "email": "alice@example.com"}

        assert (
            db.insert("users").values(row).on_conflict_do_nothing(["email"]).returning().execute().fetch_dicts() == []
        )
        updated = db.insert("users").values(row).on_conflict_do_update(["email"]).returning(["id", "name"]).execute()
        assert updated.fetch_dicts() == [{"id": 1, "name": "Alicia"}]
        db.insert("users").values(row).on_conflict_do_update(["email"], {"age": raw('"age" + 1')}).execute()
        assert db.select("users").columns(["age"]).where_equals("id", 1).execute().scalar() == 31

    def test_emulated_upsert(self, db: Database) -> None:
        query = (
            db.insert("users")
            .values({"name": "Alicia", "email": "alice@example.com"}, {"name": "Eve", "email": "eve@example.com"})
            .on_conflict_do_update(["email"])
            .returning(["name"])
            .emulate_on_conflict("id", in_transaction=True)
        )

        assert query.execute().fetch_dicts() == [{"name": "Alicia"}, {"name": "Eve"}]
        assert db.select("users").count() == 5

    def test_emulated_do_nothing(self, db: Database) -> None:
        db.insert("users").values({"name": "x", "email": "bob@example.com"}).on_conflict_do_nothing(
            ["email"]
        ).emulate_on_conflict("id").execute()

        assert names(db.select("users").where_equals("email", "bob@example.com")) == ["bob"]

    def test_unique_violation(self, db: Database) -> None:
        with pytest.raises(UniqueViolationError):
            db.insert("users").values({"name": "x", "email": "bob@example.com"}).execute()


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

            return db.select("posts").count()

        assert db.transaction(outer) == 4
        assert not db.in_transaction()

    def test_failed_transaction_rolls_back(self, db: Database) -> None:
        def fail(db: Database) -> None:
            db.delete("posts").execute()
            db.insert("users").values({"name": "x", "email": "bob@example.com"}).execute()

        with pytest.raises(UniqueViolationError):
            db.transaction(fail)

        assert db.select("posts").count() == 3


class TestDdl:
    def test_create_if_not_exists_and_drop_if_exists(self, db: Database) -> None:
        db.create_table("users").if_not_exists().integer("x").execute()
        db.drop_table("missing").if_exists().execute()
        db.drop_table("posts").execute()

        assert db.select("users").count() == 4
        assert db.query("SELECT name FROM sqlite_master WHERE name = 'posts'").fetch_dict() is None

    def test_alter_table(self, db: Database) -> None:
        db.alter_table("users").add_varchar("nickname", 50, default="none").add_integer(
            "score", not_null=True, default=0
        ).rename_column("age", "years").drop_column("created_at").execute()

        row = db.select("users").where_equals("id", 1).execute().fetch_dict()

        assert row == {
            "id": 1,
            "name": "Alice",
            "email": "alice@example.com",
            "years": 30,
            "active": 1,
            "nickname": "none",
            "score": 0,
        }

    def test_unsupported_alter(self, db: Database) -> None:
        with pytest.raises(QueryError):
            db.alter_table("users").alter_column("age", "BIGINT").execute()

    def test_indexes(self, db: Database) -> None:
        db.create_index("users", "users_name").columns(["name"]).unique().execute()
        db.create_index("users", "users_name").if_not_exists().columns(["name"]).execute()
        db.create_index("users", "active_age").columns(["age"]).where_equals("active", True).execute()
        indexes = db.query("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")

        assert {row["name"]: row["sql"] for row in indexes} == {
            "users_name": 'CREATE UNIQUE INDEX "users_name" ON "users" ("name")',
            "active_age": 'CREATE INDEX "active_age" ON "users" ("age") WHERE "active" = 1',
        }

        with pytest.raises(UniqueViolationError):
            db.insert("users").values({"name": "Alice", "email": "a2@example.com"}).execute()

        db.drop_index("users", "users_name").execute()
        db.drop_index("users", "users_name").if_exists().execute()
        db.insert("users").values({"name": "Alice", "email": "a2@example.com"}).execute()


class TestQueryFactory:
    def test_table(self, db: Database) -> None:
        tags = db.table("tags")
        tags.create().identity("id").varchar("label", not_null=True).execute()
        tags.create_index("tags_label").columns(["label"]).execute()
        tags.insert({"label": "a"}, {"label": "b"}, {"label": "c"}).execute()
        tags.update({"label": "B"}).where_equals("label", "b").execute()
        tags.delete().where_equals("label", "c").execute()
        tags.alter().add_boolean("hidden", default=False).execute()

        assert tags.select(["label", "hidden"]).order_by_asc("id").execute().fetch_dicts() == [
            {"label": "a", "hidden": 0},
            {"label": "B", "hidden": 0},
        ]

        tags.drop_index("tags_label").execute()
        tags.drop().execute()

        assert db.query("SELECT name FROM sqlite_master WHERE name LIKE 'tags%'").fetch_dicts() == []
