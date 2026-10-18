"""MySQLSchema against a live MySQL server (skipped when localhost:3306 is unreachable).

Every test runs in a fresh ``sentiencedb_test`` database, dropped afterwards.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from sentiencedb import Database, ReferentialActionEnum, current_timestamp, raw
from sentiencedb.queries.enums import TypeEnum
from sentiencedb.queries.objects.ddl import Column, ForeignKeyConstraint, Index, Type, UniqueConstraint

pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

DATABASE = "sentiencedb_test"
OTHER_DATABASE = "sentiencedb_test_other"
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
    if not server_is_up(3306):
        pytest.skip("MySQL is not reachable on localhost:3306")

    with Database.connect_mysql("mysql", username="root") as admin:
        for database in (DATABASE, OTHER_DATABASE):
            admin.exec(f"DROP DATABASE IF EXISTS {database}")
            admin.exec(f"CREATE DATABASE {database}")

        yield admin

        for database in (DATABASE, OTHER_DATABASE):
            admin.exec(f"DROP DATABASE {database}")


@pytest.fixture
def db(admin: Database) -> Iterator[Database]:
    with Database.connect_mysql(DATABASE, username="root") as db:
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
        .column("price", "DECIMAL(10, 2)")
        .unique_constraint(["name"], "authors_name")
        .execute()
    )
    (
        db.create_table("books")
        .integer("author_id", not_null=True)
        .varchar("isbn", 13, not_null=True)
        .integer("edition", 32, not_null=True, default=1)
        .varchar("title", not_null=True)
        .integer("reviewer_id")
        .primary_keys(["isbn", "edition"])
        .unique_constraint(["title", "author_id"])
        .foreign_key_constraint("author_id", "authors", "id", on_update=CASCADE, on_delete=RESTRICT)
        .foreign_key_constraint("reviewer_id", "authors", "id", on_delete=SET_NULL)
        .execute()
    )
    db.create_index("books", "books_title").columns(["title"]).execute()
    db.create_index("books", "books_author_edition").columns(["author_id", "edition"]).unique().execute()
    db.exec("CREATE INDEX books_lower_title ON books ((lower(title)))")


def without_names(items: list[Any]) -> list[Any]:
    return [replace(item, name=None) for item in items]


def describe(db: Database, table: str) -> dict[str, Any]:
    """What ``CREATE TABLE`` defines of ``table``, without the constraint names MySQL generates."""
    return {
        "columns": db.information_schema_columns(table),
        "primary_keys": db.information_schema_primary_keys(table),
        "unique_constraints": without_names(db.information_schema_unique_constraints(table)),
        "foreign_key_constraints": without_names(db.information_schema_foreign_key_constraints(table)),
    }


def copy_table(db: Database, source: str, target: str) -> None:
    """``CREATE TABLE target`` from the introspection of ``source`` (foreign key names are unique per database)."""
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
    assert {DATABASE, OTHER_DATABASE, "information_schema", "mysql"} <= set(db.information_schema_schemas())


def test_tables_of_the_current_database_only(admin: Database, db: Database) -> None:
    admin.exec(f"CREATE TABLE {OTHER_DATABASE}.elsewhere (id INTEGER)")
    db.exec("CREATE VIEW author_names AS SELECT name FROM authors")

    assert db.information_schema_tables() == ["authors", "books"]


def test_columns(db: Database) -> None:
    assert db.information_schema_columns("authors") == [
        Column("id", Type(TypeEnum.INTEGER, 64), not_null=True, generated_by_default_as_identity=True),
        Column("name", Type(TypeEnum.VARCHAR, 100), not_null=True),
        Column("bio", Type(TypeEnum.TEXT, 65535)),
        Column("active", Type(TypeEnum.BOOLEAN), not_null=True, default=raw("'1'")),
        Column("age", Type(TypeEnum.INTEGER, 32), default=raw("'0'")),
        Column("views", Type(TypeEnum.INTEGER, 64)),
        Column("score", Type(TypeEnum.FLOAT, 64)),
        Column("created_at", Type(TypeEnum.DATETIME, 6), default=raw("CURRENT_TIMESTAMP(6)")),
        Column("note", Type(TypeEnum.VARCHAR, 255), default=raw("'it''s'")),
        Column("price", "decimal(10,2)"),
    ]


def test_columns_keep_types_without_a_portable_equivalent(db: Database) -> None:
    db.exec(
        "CREATE TABLE measurements (a FLOAT, b DATETIME, c MEDIUMTEXT, d LONGTEXT, e INT UNSIGNED, f SMALLINT,"
        " g ENUM('x', 'y'), h JSON)"
    )

    assert [column.type for column in db.information_schema_columns("measurements")] == [
        Type(TypeEnum.FLOAT, 32),
        Type(TypeEnum.DATETIME, 0),
        Type(TypeEnum.TEXT, 16777215),
        Type(TypeEnum.TEXT, 4294967295),
        "int unsigned",
        "smallint",
        "enum('x','y')",
        "json",
    ]


def test_expression_defaults_keep_their_parentheses(db: Database) -> None:
    db.exec("CREATE TABLE tokens (a VARCHAR(36) DEFAULT (uuid()), b DATETIME DEFAULT NOW(), c INT DEFAULT NULL)")
    copy_table(db, "tokens", "tokens_copy")

    assert [column.default for column in db.information_schema_columns("tokens")] == [
        raw("(uuid())"),
        raw("CURRENT_TIMESTAMP"),
        None,
    ]
    assert db.information_schema_columns("tokens_copy") == db.information_schema_columns("tokens")


def test_primary_keys_in_key_order(db: Database) -> None:
    db.exec("CREATE TABLE reversed (a INTEGER, b INTEGER, c INTEGER, PRIMARY KEY (c, a))")

    assert db.information_schema_primary_keys("authors") == ["id"]
    assert db.information_schema_primary_keys("books") == ["isbn", "edition"]
    assert db.information_schema_primary_keys("reversed") == ["c", "a"]


def test_unique_constraints_include_unique_indexes(db: Database) -> None:
    """MySQL does not tell a unique constraint from a unique index."""
    assert db.information_schema_unique_constraints("authors") == [UniqueConstraint(["name"], "authors_name")]
    assert db.information_schema_unique_constraints("books") == [
        UniqueConstraint(["author_id", "edition"], "books_author_edition"),
        UniqueConstraint(["title", "author_id"], "title"),
    ]


def test_foreign_key_constraints(db: Database) -> None:
    assert db.information_schema_foreign_key_constraints("books") == [
        ForeignKeyConstraint(["author_id"], "authors", ["id"], "books_ibfk_1", CASCADE, RESTRICT),
        ForeignKeyConstraint(["reviewer_id"], "authors", ["id"], "books_ibfk_2", NO_ACTION, SET_NULL),
    ]
    assert db.information_schema_foreign_key_constraints("authors") == []


def test_foreign_keys_find_the_table_of_a_referenced_primary_key(db: Database) -> None:
    """Every primary key is called ``PRIMARY``; the referenced table must not be looked up by that name."""
    db.exec("CREATE TABLE editions (e INTEGER, i VARCHAR(13), FOREIGN KEY (i, e) REFERENCES books (isbn, edition))")

    assert db.information_schema_foreign_key_constraints("editions") == [
        ForeignKeyConstraint(["i", "e"], "books", ["isbn", "edition"], "editions_ibfk_1", NO_ACTION, NO_ACTION)
    ]


def test_foreign_keys_to_other_databases_are_qualified(admin: Database, db: Database) -> None:
    admin.exec(f"CREATE TABLE {OTHER_DATABASE}.publishers (id INTEGER PRIMARY KEY)")
    db.exec(f"CREATE TABLE contracts (publisher_id INTEGER REFERENCES {OTHER_DATABASE}.publishers (id))")

    [foreign_key] = db.information_schema_foreign_key_constraints("contracts")

    assert foreign_key.reference_table == [OTHER_DATABASE, "publishers"]


def test_indexes_skip_the_primary_key_and_expression_indexes(db: Database) -> None:
    assert db.information_schema_indexes("books") == [
        Index("books_author_edition", ["author_id", "edition"], True),
        Index("books_title", ["title"], False),
        Index("reviewer_id", ["reviewer_id"], False),
        Index("title", ["title", "author_id"], True),
    ]


def test_identifiers_are_bound_not_interpolated(db: Database) -> None:
    table, index = "we\"ird' table", "we'ird `index`"
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


def test_create_index_if_not_exists_is_always_emulated(db: Database) -> None:
    """Index names are per table: an index of the same name on another table does not count."""
    assert not db.dialect.index_exists

    db.create_index("books", "books_title").columns(["isbn"]).if_not_exists().execute()
    db.create_index("authors", "books_title").columns(["age"]).if_not_exists().execute()
    db.create_index("authors", "books_title").columns(["age"]).if_not_exists().execute()

    assert Index("books_title", ["title"], False) in db.information_schema_indexes("books")
    assert db.information_schema_indexes("authors") == [
        Index("authors_name", ["name"], True),
        Index("books_title", ["age"], False),
    ]


def test_drop_index_if_exists_is_always_emulated(db: Database) -> None:
    db.drop_index("books", "missing").if_exists().execute()
    db.drop_index("authors", "books_title").if_exists().execute()
    db.drop_index("books", "books_title").if_exists().execute()

    assert "books_title" not in [index.name for index in db.information_schema_indexes("books")]
