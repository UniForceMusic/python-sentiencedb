from __future__ import annotations

import datetime
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sentiencedb import (
    CheckViolationError,
    Database,
    Driver,
    DriverError,
    HavingGroup,
    NotNullViolationError,
    QueryError,
    ReferentialActionEnum,
    TypeEnum,
    UniqueViolationError,
    WhereGroup,
    alias,
    current_timestamp,
    expressionf,
    identifier,
    raw,
    sub_query,
)
from sentiencedb.queries.objects import Type


def debug_callback(query: str, starttime: float, error: str | None) -> None:
    elapsed = time.time() - starttime

    print(f"[SQL] {query}")
    print(f"[TIME] {elapsed:.4f}s")

    if error:
        print(f"[ERROR] {error}")

db = Database.connect_sqlite(":memory:", debug=debug_callback)
# db = Database.connect_postgresql("postgres", host="localhost", username="postgres", debug=debug_callback)
# db = Database.connect_mysql("sentiencedb", host="localhost", username="root", password="", debug=debug_callback)
# db = Database.connect_mariadb("sentiencedb", host="localhost", port=3307, username="root", debug=debug_callback)

db.create_table("users").if_not_exists().identity("id").varchar("name", not_null=True).integer("age").execute()

db.create_table("posts").if_not_exists().identity("id").varchar("title", not_null=True).text("body").integer("user_id")\
    .foreign_key_constraint("user_id", "users", "id", on_delete=ReferentialActionEnum.CASCADE).execute()

db.alter_table("users").add_varchar("email", 255).execute()

db.select("users")\
    .where("id = ? AND id = ?", [1, 2])\
    .execute()

try:
    db.alter_table("users").add_unique_constraint(["email"], name="uq_users_email").execute()
except Exception as e:
    print(f"\n[Expected] SQLite limitation: {e}")

db.insert("users").values(
    {"name": "Alice", "age": 24},
    {"name": "Bob",   "age": 25},
).execute()

print("Inserted 2 users.")

subquery = db.select("users").columns(['id'])
result = db.select("users").where_exists(subquery).execute()
users: list[dict[str, Any]] = result.fetch_dicts()
print("\nAll users after insert:")
for u in users:
    print(f"  {u}")

db.update("users").set({"age": 26}).where_equals("name", "Bob")\
    .where_group(lambda group: group.where_not_equals('name', 'Alice')).execute()
print("\nUpdated Bob's age to 26.")

result = db.select("users").where_equals("name", "Bob").execute()
print("\nBob after update:", result.fetch_dict())

db.delete("users").where_equals("name", "Alice").execute()
print("\nDeleted Alice.")

result = db.select("users").execute()
print("\nFinal users in table:")
for u in result.fetch_dicts():
    print(f"  {u}")

print("\n" + "=" * 70)
print("GIANT SELECT — every condition type + joins + group/having + unions")
print("=" * 70)

subq_active = db.select("users").columns(["id"]).where_equals("active", 1)
subq_young  = db.select("users").columns(["id"]).where_less_than("age", 30)

bigQuery = db.select("users").columns(["id", "name", "email", "age", "created_at", "score"])

subq_active = db.select("users").columns(["id"]).where_equals("active", 1)
subq_young  = db.select("users").columns(["id"]).where_less_than("age", 30)

bigQuery.distinct()

bigQuery.inner_join_table("posts", lambda join: join.on(["users", "id"], ["p", "user_id"]), alias="p")
bigQuery.left_join_table(
    "comments", lambda join: join.on(["p", "id"], ["c", "post_id"]).where_between(['c', 'id'], 0, 999), alias="c"
)
bigQuery.cross_join("sessions")

bigQuery = (
    bigQuery
    .where_equals("active", True)
    .or_where_equals("status", "pending")
    .where_not_equals("role", "banned")
    .or_where_not_equals("role", "archived")
    .where_is_null("deleted_at")
    .or_where_is_null("suspended_at")
    .where_is_not_null("email")
    .or_where_is_not_null("phone")
    .where_like("name", "Alice%")
    .or_where_like("email", "%@example.com")
    .where_not_like("name", "test%")
    .or_where_not_like("email", "%@spam.com")
    .where_starts_with("username", "admin")
    .or_where_starts_with("username", "mod")
    .where_ends_with("filename", ".pdf")
    .or_where_ends_with("filename", ".docx")
    .where_contains("bio", "engineer")
    .or_where_contains("bio", "developer")
    .where_not_contains("bio", "script kiddie")
    .or_where_not_contains("bio", "spammer")
    .where_in("id", [1, 2, 3])
    .or_where_in("id", [10, 20, 30, 40])
    .where_not_in("id", [999, 888])
    .or_where_not_in("id", [777])
    .where_less_than("age", 18)
    .or_where_less_than("age", 13)
    .where_less_than_or_equals("age", 65)
    .or_where_less_than_or_equals("age", 0)
    .where_greater_than("score", 100)
    .or_where_greater_than("score", 999)
    .where_greater_than_or_equals("score", 0)
    .or_where_greater_than_or_equals("score", 1000)
    .where_between("age", 18, 65)
    .or_where_between("age", 1, 17)
    .where_not_between("age", 0, 17)
    .or_where_not_between("age", 66, 120)
    .where_empty("middle_name")
    .or_where_empty("nickname")
    .where_not_empty("full_name")
    .or_where_not_empty("display_name")
    .where_regex("email", r"^[a-z]+@")
    .or_where_regex("phone", r"^\+?1-")
    .where_not_regex("email", r"^test@")
    .or_where_not_regex("email", r"spam@")
    .where_exists(subq_active)
    .or_where_exists(subq_young)
    .where_not_exists(subq_active)
    .or_where_not_exists(subq_young)

    .where_group(
        lambda g: (
            g
            .where_equals("plan", "premium")
            .or_where_group(
                lambda g2: (
                    g2
                    .where_equals("plan", "free")
                    .where_group(
                        lambda g3: (
                            g3
                            .where_greater_than("trial_days", 0)
                            .or_where_is_null("trial_ends")
                        )
                    )
                )
            )
        )
    )
    .or_where_not_group(
        lambda g: g.where_equals("role", "internal")
    )

    .where("EXTRACT(YEAR FROM created_at) = ?", [2026])
    .or_where("last_login IS NOT NULL")

    .where_operator("json_data", "@>", '{"vip": true}')
    .or_where_operator("point", "<@", "circle(0,0,100)")

    .group_by(["plan", "status"])

    .having_equals("plan", "enterprise")
    .or_having_equals("plan", "startup")
    .having_not_equals("status", "archived")
    .or_having_not_equals("status", "deleted")
    .having_greater_than("score", 500)
    .or_having_greater_than("score", 1000)
    .having_less_than("score", 99999)
    .having_between("age", 0, 150)
    .or_having_between("age", 18, 35)
    .having_not_between("age", 0, 17)
    .or_having_not_between("age", 100, 200)
    .having_is_null("deleted_at")
    .or_having_is_null("suspended_at")
    .having_is_not_null("email")
    .or_having_is_not_null("phone")
    .having_like("email", "%@corp.com")
    .or_having_like("email", "%@org")
    .having_not_like("email", "%@spam.com")
    .or_having_not_like("email", "%@temp")
    .having_starts_with("domain", "internal")
    .or_having_starts_with("domain", "corp")
    .having_ends_with("filename", ".csv")
    .or_having_ends_with("filename", ".xlsx")
    .having_contains("description", "urgent")
    .or_having_contains("description", "priority")
    .having_not_contains("description", "obsolete")
    .or_having_not_contains("description", "deprecated")
    .having_in("region", ["US", "EU", "APAC"])
    .or_having_in("region", ["LATAM"])
    .having_not_in("region", ["ANON"])
    .or_having_not_in("region", ["BLOCKED"])
    .having_empty("middle_name")
    .or_having_empty("nickname")
    .having_not_empty("full_name")
    .or_having_not_empty("display_name")
    .having_regex("email", r"\.com$")
    .or_having_regex("email", r"\.org$")
    .having_not_regex("email", r"\.test$")
    .or_having_not_regex("email", r"\.local$")
    .having_exists(subq_active)
    .or_having_exists(subq_young)
    .having_not_exists(subq_active)
    .or_having_not_exists(subq_young)

    .having_group(
        lambda g: (
            g
            .having_greater_than("score", 1000)
            .or_having_group(
                lambda g2: g2
                .having_equals("vip", True)
                .having_in("tier", ["gold", "platinum"])
            )
        )
    )
    .or_having_not_group(
        lambda g: g.having_equals("status", "internal")
    )
    .having("AVG(rating) >= ?", [4.5])
    .or_having("MAX(logins) > 100")
    .having("json_meta @> '{\"premium\": true}'")
    .or_having("tsvector @@ to_tsquery('urgent')")

    .order_by_asc("plan")
    .order_by_desc("score")

    .limit(50)
    .offset(10)

    .union(
        db.select("archived_users")
        .columns(["id", "name", "email", "age", "archived_at", "score"])
        .where_greater_than("score", 500)
    )
    .union_all(
        db.select("legacy_users")
        .columns(["id", "name", "email", "age", "created_at", "score"])
        .where_equals("migrated", False)
    )
)
print(f"\n{'-' * 70}")
print("GENERATED SQL:")
print(bigQuery.to_sql())
print(f"{'-' * 70}\n")

print("\n" + "=" * 70)
print("RAW SQL — exec, query, prepared (? and :name), emulate_prepare, to_query_with_params")
print("=" * 70)

db.update("users").set({"email": "bob@example.com"}).where_equals("name", "Bob").execute()
db.insert("users").values(
    {"name": "Carol", "age": 31, "email": "carol@example.com"},
    {"name": "Dave", "age": 17, "email": "dave@example.org"},
    {"name": "Erin", "age": 45, "email": None},
).execute()
print("\nInserted Carol, Dave and Erin. Last insert id:", db.last_insert_id())

print("\nUser count:", db.query("SELECT count(*) AS n FROM users").scalar())
result = db.prepared("SELECT name, age FROM users WHERE age > ? ORDER BY name", [30])
print("\nUsers older than 30 (? placeholder):")
for u in result.fetch_dicts():
    print(f"  {u}")
result = db.prepared("SELECT name FROM users WHERE age < :age ORDER BY name", {"age": 18})
print("\nUsers younger than 18 (:name placeholder):", result.fetch_dicts())
print("\nEmulated prepare:", db.prepared("SELECT ? AS answer", [42], emulate_prepare=True).scalar())

qwp = db.select("users").columns(["name"]).where_greater_than("age", 30).to_query_with_params()
print(f"\n{'-' * 70}")
print("QUERY WITH PARAMS:")
print(qwp.query)
print(qwp.params)
print(f"{'-' * 70}\n")

print("\n" + "=" * 70)
print("JOINS + AGGREGATES — inner/left join, group by, having, order, limit, offset")
print("=" * 70)


def has_posts(group: HavingGroup) -> HavingGroup:
    return group.having_greater_than(raw("count(p.id)"), 0)


ids = {u["name"]: u["id"] for u in db.select("users").columns(["id", "name"]).execute()}
db.insert("posts").values(
    {"title": "Hello", "body": "First post", "user_id": ids["Bob"]},
    {"title": "Again", "body": "Second post", "user_id": ids["Bob"]},
    {"title": "Hi", "body": None, "user_id": ids["Carol"]},
).execute()
print("\nInserted 3 posts.")

posts_per_user = (
    db.select_table("users", "u")
    .columns([["u", "name"], alias(raw("count(p.id)"), "posts")])
    .left_join_table("posts", lambda join: join.on(["p", "user_id"], ["u", "id"]), alias="p")
    .group_by([["u", "name"]])
    .having_group(has_posts)
    .order_by_desc(raw("count(p.id)"))
    .order_by_asc(["u", "name"])
    .limit(10)
    .offset(0)
)
print(f"\n{'-' * 70}")
print("GENERATED SQL:")
print(posts_per_user.to_sql())
print(f"{'-' * 70}\n")
print("\nPosts per user (LEFT JOIN + GROUP BY + HAVING):")
for u in posts_per_user.execute().fetch_dicts():
    print(f"  {u}")

result = (
    db.select("users")
    .columns([["users", "name"], ["posts", "title"]])
    .inner_join(
        "posts",
        lambda join: join.on(["posts", "user_id"], ["users", "id"]).where_is_not_null(["posts", "body"]),
    )
    .order_by_asc(["posts", "id"])
    .execute()
)
print("\nPosts with a body (INNER JOIN with an extra join condition):")
for u in result.fetch_dicts():
    print(f"  {u}")

result = db.select("users").columns(["name"]).join("INNER JOIN posts ON posts.user_id = users.id").execute()
print("\nRaw JOIN:", result.fetch_dicts())

result = db.select("users").columns(["name", "age"]).order_by_desc("age").limit(2).offset(1).execute()
print("\nSecond and third oldest (ORDER BY + LIMIT + OFFSET):", result.fetch_dicts())

print("\n" + "=" * 70)
print("WHERE VARIANTS — run against real rows")
print("=" * 70)


def active_adults(group: WhereGroup) -> WhereGroup:
    return group.where_greater_than_or_equals("age", 18).where_group(
        lambda inner: inner.where_ends_with("email", ".com").or_where_is_null("email")
    )


where_examples = [
    ("where_in", db.select("users").where_in("name", ["Bob", "Carol"]).where_not_equals("name", "Bob")),
    ("where_not_in", db.select("users").where_not_in("name", ["Bob", "Carol"])),
    ("where_between", db.select("users").where_between("age", 18, 40)),
    ("where_not_between", db.select("users").where_not_between("age", 18, 40)),
    ("where_like", db.select("users").where_like("email", "%@example.com")),
    ("where_starts_with (case insensitive)", db.select("users").where_starts_with("email", "CAROL", True)),
    ("where_ends_with", db.select("users").where_ends_with("email", ".org")),
    ("where_contains", db.select("users").where_contains("name", "ar")),
    ("where_not_contains", db.select("users").where_not_contains("name", "ar")),
    ("where_is_null", db.select("users").where_is_null("email")),
    ("where_empty", db.select("users").where_empty("email")),
    ("where_regex (flag i)", db.select("users").where_regex("name", "^[bd]", "i")),
    ("where_not_regex", db.select("users").where_not_regex("name", "^[BD]")),
    ("where_glob", db.select("users").where_glob("name", "[CE]*")),
    ("where_group callback", db.select("users").where_group(active_adults)),
    ("where_not_group", db.select("users").where_not_group(lambda g: g.where_less_than("age", 30))),
    ("where_in sub-select", db.select("users").where_in("id", db.select("posts").columns(["user_id"]))),
    (
        "correlated where_exists",
        db.select("users").where_exists(
            db.select("posts").where_equals(["posts", "user_id"], identifier(["users", "id"])).where_is_null("body")
        ),
    ),
    ("where with :name params", db.select("users").where("age > :min", {"min": 30})),
    ("wheref", db.select("users").wheref("%s >= %d", identifier("age"), 31)),
    ("where_operator", db.select("users").where_operator("age", "<>", 26)),
]
for label, query in where_examples:
    names = [u["name"] for u in query.columns(["name"]).order_by_asc("name").execute()]
    print(f"  {label}: {names}")

print("\n" + "=" * 70)
print("SELECT EXTRAS — distinct, union, sub-query, count, scalar, fetch_object")
print("=" * 70)

result = db.select("posts").columns(["user_id"]).distinct().order_by_asc("user_id").execute()
print("\nDISTINCT authors:", result.fetch_dicts())

try:
    result = db.select("posts").columns(["user_id", "title"]).distinct(["user_id"]).order_by_asc("user_id").execute()
    print("\nDISTINCT ON (user_id):", result.fetch_dicts())
except QueryError as e:
    print(f"\n[Expected] DISTINCT ON is PostgreSQL only: {e}")

result = (
    db.select("users")
    .columns(["name"])
    .where_less_than("age", 18)
    .union_all(db.select("users").columns(["name"]).where_greater_than("age", 40))
    .execute()
)
print("\nUNION ALL of minors and over-40s:", result.fetch_dicts())

adults = sub_query(db.select("users").where_greater_than_or_equals("age", 18), "adults")
print("\nAdults (SELECT count(*) FROM sub-query):", db.select(adults).columns([raw("count(*)")]).execute().scalar())
print("\nUsers with an email (count()):", db.select("users").where_is_not_null("email").count())

oldest = db.select("users").columns(["name", "age"]).order_by_desc("age").limit(1).execute().fetch_object()
print("\nOldest user as an object:", oldest)
print("\nResult columns:", db.select("users").columns(["id", "name"]).limit(1).execute().columns())

print("\n" + "=" * 70)
print("INSERT — RETURNING, upserts (do update / do nothing), emulated upsert")
print("=" * 70)

db.drop_table("settings").if_exists().execute()
(
    db.create_table("settings")
    .identity("id")
    .varchar("name", 100, not_null=True)
    .text("value")
    .integer("hits", not_null=True, default=0)
    .unique_constraint(["name"], name="uq_settings_name")
    .execute()
)

result = (
    db.insert("settings")
    .values({"name": "theme", "value": "dark"})
    .returning(["id", "name"])
    .last_insert_id("id")
    .execute()
)
print("\nInserted with RETURNING:", result.fetch_dicts())

upsert = (
    db.insert("settings")
    .values({"name": "theme", "value": "light"}, {"name": "lang", "value": "en"})
    .on_conflict_do_update(["name"])
)
print(f"\n{'-' * 70}")
print("GENERATED SQL:")
print(upsert.to_sql())
print(f"{'-' * 70}\n")
upsert.execute()

(
    db.insert("settings")
    .values({"name": "theme", "value": "ignored"})
    .on_conflict_do_update(["name"], {"hits": expressionf("%s + 1", identifier(["settings", "hits"]))})
    .execute()
)

db.insert("settings").values({"name": "lang", "value": "nl"}).on_conflict_do_nothing(["name"]).execute()

result = (
    db.insert("settings")
    .values({"name": "lang", "value": "de"}, {"name": "tz", "value": "UTC"})
    .on_conflict_do_update(["name"])
    .emulate_on_conflict("id")
    .returning(["name", "value"])
    .execute()
)
print("\nEmulated upsert with RETURNING:", result.fetch_dicts())

result = db.select("settings").columns(["name", "value", "hits"]).order_by_asc("id").execute()
print("\nSettings after upserts:")
for u in result.fetch_dicts():
    print(f"  {u}")

print("\n" + "=" * 70)
print("UPDATE / DELETE — expressions, sub-selects, RETURNING")
print("=" * 70)

db.update("users").set({"age": expressionf("%s + %d", identifier("age"), 1)}).where_equals("name", "Dave").execute()
carols_email = db.select("users").columns(["email"]).where_equals("name", "Carol")
db.update("posts").set({"body": carols_email}).where_is_null("body").execute()
result = db.select("posts").columns(["title", "body"]).order_by_asc("id").execute()
print("\nPosts after UPDATE with a sub-select value:", result.fetch_dicts())

try:
    update_lang = db.update("settings").set({"value": "fr"}).where_equals("name", "lang")
    result = update_lang.returning(["name", "value"]).execute()
    print("\nUpdated with RETURNING:", result.fetch_dicts())
except QueryError as e:
    print(f"\n[Expected] UPDATE ... RETURNING is not supported on {db.dialect.driver}: {e}")

try:
    result = db.delete("settings").where_equals("name", "tz").returning(["id", "name"]).execute()
    print("\nDeleted with RETURNING:", result.fetch_dicts())
except QueryError as e:
    print(f"\n[Expected] DELETE ... RETURNING is not supported on {db.dialect.driver}: {e}")
    db.delete("settings").where_equals("name", "tz").execute()

print("\n" + "=" * 70)
print("DDL — every column helper, constraints, foreign keys, alter table, indexes")
print("=" * 70)

db.drop_table("events").if_exists().execute()
create_events = (
    db.create_table("events")
    .if_not_exists()
    .identity("id")
    .integer("user_id", not_null=True)
    .boolean("published", not_null=True, default=False)
    .integer("attendees", bits=32, default=0)
    .float("price")
    .float("rating", bits=32)
    .varchar("title", 100, not_null=True)
    .text("description")
    .datetime("starts_at", not_null=True)
    .datetime("created_at", default=current_timestamp())
    .column("code", Type(TypeEnum.VARCHAR, 20))
    .unique_constraint(["title", "starts_at"], name="uq_events_title_starts_at")
    .foreign_key_constraint("user_id", "users", "id", name="fk_events_user", on_delete=ReferentialActionEnum.CASCADE)
    .constraint("CHECK (attendees >= 0)")
)
print(f"\n{'-' * 70}")
print("GENERATED SQL:")
print(create_events.to_sql())
print(f"{'-' * 70}\n")
create_events.execute()

db.insert("events").values(
    {
        "user_id": ids["Carol"],
        "published": True,
        "attendees": 12,
        "price": 12.5,
        "rating": 4.5,
        "title": "Launch party",
        "description": "Drinks and demos",
        "starts_at": datetime.datetime(2026, 11, 1, 19, 30),
        "code": "LP-2026",
    }
).execute()
event = db.select("events").columns(["title", "published", "price", "starts_at"]).execute().fetch_dict()
print("\nEvent as stored:", event)
if event is not None:
    print("Parsed published:", db.dialect.parse_bool(event["published"]))

alter_events = (
    db.alter_table("events")
    .add_boolean("cancelled", default=False)
    .add_integer("capacity", bits=32)
    .rename_column("description", "summary")
    .drop_column("code")
)
print(f"\n{'-' * 70}")
print("GENERATED SQL:")
for sql in alter_events.to_sql():
    print(sql)
print(f"{'-' * 70}\n")
alter_events.execute()

try:
    column_sql = "TYPE BIGINT" if db.dialect.driver == Driver.PGSQL else "BIGINT"
    db.alter_table("events").alter_column("capacity", column_sql).execute()
    print("\nAltered column 'capacity' to BIGINT.")
except QueryError as e:
    print(f"\n[Expected] SQLite limitation: {e}")

create_index = db.create_index("events", "idx_events_starts_at").if_not_exists().columns(["starts_at", "user_id"])
print(f"\n{'-' * 70}")
print("GENERATED SQL:")
print(create_index.to_sql())
print(f"{'-' * 70}\n")
create_index.execute()

try:
    partial_index = (
        db.create_index("events", "uq_events_published_title")
        .unique()
        .columns(["title"])
        .where_equals("published", True)
    )
    print("\nPartial index:", partial_index.to_sql())
    partial_index.execute()
except QueryError as e:
    print(f"\n[Expected] MySQL / MariaDB have no partial indexes: {e}")

print("\n" + "=" * 70)
print("SCHEMA INTROSPECTION — information_schema_*")
print("=" * 70)

print("\nSchemas:", db.information_schema_schemas())
print("Tables:", db.information_schema_tables())
print("Table 'events' exists:", db.information_schema_table_exists("events"))
print("Index 'idx_events_starts_at' exists:", db.information_schema_index_exists("events", "idx_events_starts_at"))
print("Primary keys of 'events':", db.information_schema_primary_keys("events"))
print("\nColumns of 'events':")
for column in db.information_schema_columns("events"):
    print(f"  {column}")
print("\nUnique constraints of 'events':")
for unique in db.information_schema_unique_constraints("events"):
    print(f"  {unique}")
print("\nForeign keys of 'events':")
for foreign_key in db.information_schema_foreign_key_constraints("events"):
    print(f"  {foreign_key}")
print("\nIndexes of 'events':")
for index in db.information_schema_indexes("events"):
    print(f"  {index}")

db.drop_index("events", "idx_events_starts_at").if_exists().execute()
print("\nIndex exists after drop:", db.information_schema_index_exists("events", "idx_events_starts_at"))

print("\n" + "=" * 70)
print("db.table() — a QueryFactory bound to one table")
print("=" * 70)

settings = db.table("settings")
settings.insert({"name": "timezone", "value": "UTC"}).execute()
settings.update({"value": "Europe/Amsterdam"}).where_equals("name", "timezone").execute()
print("\nTimezone setting:", settings.select(["name", "value"]).where_equals("name", "timezone").execute().fetch_dict())
print("Columns:", [column.name for column in settings.columns()])
print("Primary keys:", settings.primary_keys())
settings.delete().where_equals("name", "timezone").execute()
print("Settings left:", settings.select().count())

print("\n" + "=" * 70)
print("TRANSACTIONS — transaction(), rollback on error, savepoints")
print("=" * 70)


def add_currency(tx: Database) -> int:
    tx.insert("settings").values({"name": "currency", "value": "EUR"}).execute()
    return tx.select("settings").count()


print("\nCommitted, settings now:", db.transaction(add_currency))


def delete_everything(tx: Database) -> None:
    tx.delete("settings").execute()
    raise RuntimeError("abort")


try:
    db.transaction(delete_everything)
except RuntimeError as e:
    print(f"\n[Expected] Transaction rolled back: {e}")
print("Settings after rollback:", db.select("settings").count())

db.begin_transaction()
db.insert("settings").values({"name": "kept", "value": "yes"}).execute()
db.begin_transaction("before_discarded")
db.insert("settings").values({"name": "discarded", "value": "no"}).execute()
db.rollback_transaction()
db.commit_transaction()
result = db.select("settings").columns(["name"]).where_in("name", ["kept", "discarded"]).execute()
print("\nIn transaction:", db.in_transaction(), "| rows after savepoint rollback:", result.fetch_dicts())

print("\n" + "=" * 70)
print("ERRORS — driver errors mapped to sentiencedb exceptions")
print("=" * 70)

try:
    db.insert("settings").values({"name": "theme", "value": "duplicate"}).execute()
except UniqueViolationError as e:
    print(f"\n[Expected] UniqueViolationError (code={e.code}, sqlstate={e.sqlstate}): {e.query}")

try:
    db.insert("events").values(
        {"user_id": ids["Bob"], "title": None, "starts_at": datetime.datetime(2026, 12, 1, 20, 0)}
    ).execute()
except NotNullViolationError as e:
    print(f"\n[Expected] NotNullViolationError: {e}")

try:
    db.insert("events").values(
        {"user_id": ids["Bob"], "title": "Negative", "attendees": -1, "starts_at": datetime.datetime(2026, 12, 1)}
    ).execute()
except CheckViolationError as e:
    print(f"\n[Expected] CheckViolationError: {e}")

try:
    db.query("SELEC 1")
except DriverError as e:
    print(f"\n[Expected] {type(e).__name__}: {e}")

try:
    db.update("users").set({}).execute()
except QueryError as e:
    print(f"\n[Expected] QueryError before anything is sent: {e}")

print("\n" + "=" * 70)
print("THREADING — one connection per thread, released by db.connection()")
print("=" * 70)

db.drop_table("hits").if_exists().execute()
db.create_table("hits").identity("id").integer("worker").execute()


def work(worker: int) -> None:
    with db.connection():
        db.insert("hits").values({"worker": worker}).execute()


with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(work, range(8)))
print("\nHits inserted by 8 tasks on 4 threads:", db.select("hits").count())

print("\nDropping demo tables...")
db.drop_table("hits").if_exists().execute()
db.drop_table("events").if_exists().execute()
db.drop_table("settings").if_exists().execute()

print("\nDropping 'posts' table...")
db.drop_table("posts").if_exists().execute()

print("\nDropping 'users' table...")
db.drop_table("users").if_exists().execute()

try:
    result = db.select("users").execute()
    print("\nUsers after drop:")
    for u in result.fetch_dicts():
        print(f"  {u}")
except Exception as e:
    print(f"\n[Expected] Table 'users' no longer exists: {e}")
