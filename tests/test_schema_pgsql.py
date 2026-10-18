"""PgSQLSchema against a live PostgreSQL server (skipped when localhost:5432 is unreachable).

Every test runs in a fresh ``sentiencedb_test`` schema, dropped afterwards.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from sentiencedb import Database, ReferentialActionEnum, current_timestamp, raw
from sentiencedb.options import Options
from sentiencedb.queries.enums import TypeEnum
from sentiencedb.queries.objects.ddl import Column, ForeignKeyConstraint, Index, Type, UniqueConstraint
from sentiencedb.schemas import PgSQLSchema, SQLSchema

pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

SCHEMA = "sentiencedb_test"
OTHER_SCHEMA = "sentiencedb_test_other"
CASCADE = ReferentialActionEnum.CASCADE
NO_ACTION = ReferentialActionEnum.NO_ACTION
RESTRICT = ReferentialActionEnum.RESTRICT
SET_NULL = ReferentialActionEnum.SET_NULL


def server_is_up(port: int) -> bool:
    try:
        socket.create_connection(("localhost", port), timeout=0.5).close()
    except OSError:
        return False

    return True


@pytest.fixture
def admin() -> Iterator[Database]:
    if not server_is_up(5432):
        pytest.skip("PostgreSQL is not reachable on localhost:5432")

    with Database.connect_postgresql("postgres", username="postgres") as admin:
        for schema in (SCHEMA, OTHER_SCHEMA):
            admin.exec(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            admin.exec(f"CREATE SCHEMA {schema}")

        yield admin

        for schema in (SCHEMA, OTHER_SCHEMA):
            admin.exec(f"DROP SCHEMA {schema} CASCADE")


def connect(options: Options | None = None) -> Database:
    return Database.connect_postgresql(
        "postgres", username="postgres", options={"search_path": SCHEMA, **(options or {})}
    )


@pytest.fixture
def db(admin: Database) -> Iterator[Database]:
    with connect() as db:
        create_tables(db)
        yield db


def create_tables(db: Database) -> None:
    (
        db.create_table("authors")
        .identity("id")
        .varchar("name", 100, not_null=True)
        .text("bio")
        .boolean("active", not_null=True, default=True)
        .integer("age", 32, default=0)
        .integer("views")
        .float("score")
        .datetime("created_at", default=current_timestamp())
        .varchar("note", default="it's")
        .column("price", "NUMERIC(10, 2)")
        .unique_constraint(["name"], "authors_name")
        .execute()
    )
    (
        db.create_table("books")
        .integer("author_id", 32, not_null=True)
        .varchar("isbn", 13, not_null=True)
        .integer("edition", 32, not_null=True, default=1)
        .varchar("title", not_null=True)
        .integer("reviewer_id", 32)
        .primary_keys(["isbn", "edition"])
        .unique_constraint(["title", "author_id"])
        .foreign_key_constraint("author_id", "authors", "id", on_update=CASCADE, on_delete=RESTRICT)
        .foreign_key_constraint("reviewer_id", "authors", "id", on_delete=SET_NULL)
        .execute()
    )
    db.create_index("books", "books_title").columns(["title"]).execute()
    db.create_index("books", "books_author_edition").columns(["author_id", "edition"]).unique().execute()
    db.create_index("books", "books_recent").columns(["title"]).where_greater_than("edition", 1).execute()
    db.exec("CREATE INDEX books_lower_title ON books (lower(title))")


def without_names(items: list[Any]) -> list[Any]:
    return [replace(item, name=None) for item in items]


def describe(db: Database, table: str) -> dict[str, Any]:
    """What ``CREATE TABLE`` defines of ``table``, without the constraint names PostgreSQL generates."""
    return {
        "columns": db.information_schema_columns(table),
        "primary_keys": db.information_schema_primary_keys(table),
        "unique_constraints": without_names(db.information_schema_unique_constraints(table)),
        "foreign_key_constraints": without_names(db.information_schema_foreign_key_constraints(table)),
    }


def copy_table(db: Database, source: str, target: str) -> None:
    """``CREATE TABLE target`` from the introspection of ``source`` (constraint names are unique per schema)."""
    query = db.create_table(target).primary_keys(db.information_schema_primary_keys(source))

    for column in db.information_schema_columns(source):
        query.column(column.name, column.type, column.not_null, column.default, column.generated_by_default_as_identity)

    for unique in db.information_schema_unique_constraints(source):
        query.unique_constraint(unique.columns)

    for foreign_key in db.information_schema_foreign_key_constraints(source):
        query.foreign_key_constraint(
            foreign_key.columns,
            foreign_key.reference_table,
            foreign_key.reference_columns,
            on_update=foreign_key.on_update,
            on_delete=foreign_key.on_delete,
        )

    query.execute()


def test_schemas(db: Database) -> None:
    assert {SCHEMA, OTHER_SCHEMA, "public", "information_schema"} <= set(db.information_schema_schemas())


def test_tables_of_the_current_schema_only(admin: Database, db: Database) -> None:
    admin.exec(f"CREATE TABLE {OTHER_SCHEMA}.elsewhere (id INTEGER)")
    db.exec("CREATE VIEW author_names AS SELECT name FROM authors")

    assert db.information_schema_tables() == ["authors", "books"]


def test_columns(db: Database) -> None:
    assert db.information_schema_columns("authors") == [
        Column("id", Type(TypeEnum.INTEGER, 64), not_null=True, generated_by_default_as_identity=True),
        Column("name", Type(TypeEnum.VARCHAR, 100), not_null=True),
        Column("bio", Type(TypeEnum.TEXT)),
        Column("active", Type(TypeEnum.BOOLEAN), not_null=True, default=raw("true")),
        Column("age", Type(TypeEnum.INTEGER, 32), default=raw("0")),
        Column("views", Type(TypeEnum.INTEGER, 64)),
        Column("score", Type(TypeEnum.FLOAT, 64)),
        Column("created_at", Type(TypeEnum.DATETIME, 6), default=raw("CURRENT_TIMESTAMP")),
        Column("note", Type(TypeEnum.VARCHAR, 255), default=raw("'it''s'::character varying")),
        Column("price", "numeric(10,2)"),
    ]


def test_columns_keep_types_without_a_portable_equivalent(db: Database) -> None:
    db.exec(
        "CREATE TABLE measurements (a REAL, b TIMESTAMP(3), c TIMESTAMPTZ, d VARCHAR, e SMALLINT, f INTEGER[], g JSONB)"
    )

    assert [column.type for column in db.information_schema_columns("measurements")] == [
        Type(TypeEnum.FLOAT, 32),
        Type(TypeEnum.DATETIME, 3),
        "timestamp with time zone",
        "character varying",
        "smallint",
        "integer[]",
        "jsonb",
    ]


@pytest.mark.usefixtures("admin")
def test_identity_columns() -> None:
    with connect({"use_serials": False}) as db:
        if not db.dialect.generated_by_default_as_identity:
            pytest.skip("identity columns need PostgreSQL >= 10")

        db.exec("CREATE TABLE identities (a INTEGER GENERATED ALWAYS AS IDENTITY, b SERIAL)")
        db.create_table("identities_copy").identity("a", 32).execute()

        assert db.information_schema_columns("identities") == [
            Column("a", Type(TypeEnum.INTEGER, 32), not_null=True, generated_by_default_as_identity=True),
            Column("b", Type(TypeEnum.INTEGER, 32), not_null=True, generated_by_default_as_identity=True),
        ]
        assert db.information_schema_columns("identities_copy") == db.information_schema_columns("identities")[:1]


def test_identity_columns_with_serials(db: Database) -> None:
    db.create_table("serials").identity("a", 32).execute()

    assert db.query("SELECT column_default FROM information_schema.columns WHERE table_name = 'serials'").scalar() == (
        "nextval('serials_a_seq'::regclass)"
    )
    assert db.information_schema_columns("serials") == [
        Column("a", Type(TypeEnum.INTEGER, 32), not_null=True, generated_by_default_as_identity=True)
    ]


def test_primary_keys_in_key_order(db: Database) -> None:
    db.exec("CREATE TABLE reversed (a INTEGER, b INTEGER, c INTEGER, PRIMARY KEY (c, a))")

    assert db.information_schema_primary_keys("authors") == ["id"]
    assert db.information_schema_primary_keys("books") == ["isbn", "edition"]
    assert db.information_schema_primary_keys("reversed") == ["c", "a"]


def test_unique_constraints(db: Database) -> None:
    assert db.information_schema_unique_constraints("authors") == [UniqueConstraint(["name"], "authors_name")]
    assert db.information_schema_unique_constraints("books") == [
        UniqueConstraint(["title", "author_id"], "books_title_author_id_key")
    ]


def test_foreign_key_constraints(db: Database) -> None:
    assert db.information_schema_foreign_key_constraints("books") == [
        ForeignKeyConstraint(["author_id"], "authors", ["id"], "books_author_id_fkey", CASCADE, RESTRICT),
        ForeignKeyConstraint(["reviewer_id"], "authors", ["id"], "books_reviewer_id_fkey", NO_ACTION, SET_NULL),
    ]
    assert db.information_schema_foreign_key_constraints("authors") == []


def test_foreign_keys_keep_the_reference_column_order(db: Database) -> None:
    db.exec("CREATE TABLE editions (e INTEGER, i VARCHAR(13), FOREIGN KEY (e, i) REFERENCES books (edition, isbn))")

    assert db.information_schema_foreign_key_constraints("editions") == [
        ForeignKeyConstraint(["e", "i"], "books", ["edition", "isbn"], "editions_e_i_fkey", NO_ACTION, NO_ACTION)
    ]


def test_foreign_keys_to_other_schemas_are_qualified(admin: Database, db: Database) -> None:
    admin.exec(f"CREATE TABLE {OTHER_SCHEMA}.publishers (id INTEGER PRIMARY KEY)")
    db.exec(f"CREATE TABLE contracts (publisher_id INTEGER REFERENCES {OTHER_SCHEMA}.publishers (id))")

    [foreign_key] = db.information_schema_foreign_key_constraints("contracts")

    assert foreign_key.reference_table == [OTHER_SCHEMA, "publishers"]


def test_generic_information_schema_foreign_keys_match_the_catalog(db: Database) -> None:
    db.exec("CREATE TABLE editions (e INTEGER, i VARCHAR(13), FOREIGN KEY (e, i) REFERENCES books (edition, isbn))")

    for table in ("books", "editions"):
        generic = SQLSchema.foreign_key_constraints(PgSQLSchema(), db, db.dialect, table)

        assert generic == db.information_schema_foreign_key_constraints(table)


def test_indexes_skip_the_primary_key_and_expression_indexes(db: Database) -> None:
    db.exec("CREATE INDEX books_covering ON books (isbn) INCLUDE (title)")

    assert db.information_schema_indexes("books") == [
        Index("books_author_edition", ["author_id", "edition"], True),
        Index("books_covering", ["isbn"], False),
        Index("books_recent", ["title"], False),
        Index("books_title", ["title"], False),
        Index("books_title_author_id_key", ["title", "author_id"], True),
    ]


def test_identifiers_are_bound_not_interpolated(db: Database) -> None:
    table, index = "we\"ird' table", 'we\'ird "index"'
    db.create_table(table).integer("a b", 32).execute()
    db.create_index(table, index).columns(["a b"]).execute()

    assert db.information_schema_columns(table) == [Column("a b", Type(TypeEnum.INTEGER, 32))]
    assert db.information_schema_indexes(table) == [Index(index, ["a b"], False)]


@pytest.mark.parametrize("table", ["authors", "books"])
def test_round_trip(db: Database, table: str) -> None:
    copy_table(db, table, f"{table}_copy")

    assert describe(db, f"{table}_copy") == describe(db, table)


def test_emulated_create_table_if_not_exists(db: Database) -> None:
    db.create_table("authors").if_not_exists().emulate_if_not_exists().integer("other").execute()
    db.create_table("tags").if_not_exists().emulate_if_not_exists().integer("id").execute()

    assert "other" not in [column.name for column in db.information_schema_columns("authors")]
    assert db.information_schema_tables() == ["authors", "books", "tags"]


def test_emulated_drop_table_if_exists(db: Database) -> None:
    db.drop_table("missing").if_exists().emulate_if_exists().execute()
    db.create_table("tags").integer("id").execute()
    db.drop_table("tags").if_exists().emulate_if_exists().execute()

    assert db.information_schema_tables() == ["authors", "books"]


def test_emulated_create_index_if_not_exists(db: Database) -> None:
    db.create_index("books", "books_title").columns(["isbn"]).if_not_exists().emulate_if_not_exists().execute()
    db.create_index("authors", "books_title").columns(["bio"]).if_not_exists().emulate_if_not_exists().execute()
    db.create_index("authors", "authors_bio").columns(["bio"]).if_not_exists().emulate_if_not_exists().execute()

    assert Index("books_title", ["title"], False) in db.information_schema_indexes("books")
    assert db.information_schema_indexes("authors") == [
        Index("authors_bio", ["bio"], False),
        Index("authors_name", ["name"], True),
    ]


def test_emulated_drop_index_if_exists(db: Database) -> None:
    db.drop_index("books", "missing").if_exists().emulate_if_exists().execute()
    db.drop_index("books", "books_title").if_exists().emulate_if_exists().execute()

    assert "books_title" not in [index.name for index in db.information_schema_indexes("books")]


def test_existence_checks_and_other_schemas(db: Database) -> None:
    """Index names are unique per schema: an index on any table of the schema counts."""
    db.create_table([OTHER_SCHEMA, "extra"]).integer("id").execute()
    db.create_index([OTHER_SCHEMA, "extra"], "extra_id").columns("id").execute()

    assert db.information_schema_tables(OTHER_SCHEMA) == ["extra"]
    assert (db.information_schema_table_exists("authors"), db.information_schema_table_exists("extra")) == (True, False)
    assert db.information_schema_table_exists("extra", OTHER_SCHEMA)
    assert db.information_schema_index_exists("authors", "books_title")
    assert not db.information_schema_index_exists("extra", "extra_id")
    assert db.information_schema_index_exists("authors", "extra_id", OTHER_SCHEMA)

    db.create_table([OTHER_SCHEMA, "extra"]).if_not_exists().emulate_if_not_exists().integer("id").execute()
    db.drop_index([OTHER_SCHEMA, "extra"], "missing").if_exists().emulate_if_exists().execute()
