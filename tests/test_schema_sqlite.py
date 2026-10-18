"""SQLiteSchema against a real SQLite database: introspection, round trips and emulated IF [NOT] EXISTS."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sentiencedb import Database, ReferentialActionEnum, current_timestamp, raw
from sentiencedb.queries.enums import TypeEnum
from sentiencedb.queries.objects.ddl import Column, ForeignKeyConstraint, Index, Type, UniqueConstraint

CASCADE = ReferentialActionEnum.CASCADE
NO_ACTION = ReferentialActionEnum.NO_ACTION
RESTRICT = ReferentialActionEnum.RESTRICT
SET_NULL = ReferentialActionEnum.SET_NULL


@pytest.fixture
def db() -> Iterator[Database]:
    with Database.connect_sqlite() as db:
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


def describe(db: Database, table: str) -> dict[str, Any]:
    """What ``CREATE TABLE`` defines of ``table``."""
    return {
        "columns": db.information_schema_columns(table),
        "primary_keys": db.information_schema_primary_keys(table),
        "unique_constraints": db.information_schema_unique_constraints(table),
        "foreign_key_constraints": db.information_schema_foreign_key_constraints(table),
    }


def copy_table(db: Database, source: str, target: str) -> None:
    """``CREATE TABLE target`` from the introspection of ``source``."""
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


def test_schemas_are_database_files(tmp_path: Path) -> None:
    path = tmp_path / "main.sqlite"

    with Database.connect_sqlite(str(path)) as db:
        db.exec(f"ATTACH DATABASE '{tmp_path / 'other.sqlite'}' AS other")

        assert db.information_schema_schemas() == [str(path), str(tmp_path / "other.sqlite")]


def test_tables_exclude_sqlite_internals(db: Database) -> None:
    assert db.query("SELECT count(*) FROM sqlite_master WHERE name = 'sqlite_sequence'").scalar() == 1
    assert db.information_schema_tables() == ["authors", "books"]


def test_columns(db: Database) -> None:
    assert db.information_schema_columns("authors") == [
        Column("id", Type(TypeEnum.INTEGER, 32), generated_by_default_as_identity=True),
        Column("name", Type(TypeEnum.VARCHAR, 100), not_null=True),
        Column("bio", Type(TypeEnum.TEXT)),
        Column("active", Type(TypeEnum.BOOLEAN), not_null=True, default=raw("1")),
        Column("age", Type(TypeEnum.INTEGER, 32), default=raw("0")),
        Column("views", Type(TypeEnum.INTEGER, 64)),
        Column("score", Type(TypeEnum.FLOAT, 64)),
        Column("created_at", Type(TypeEnum.DATETIME), default=raw("CURRENT_TIMESTAMP")),
        Column("note", Type(TypeEnum.VARCHAR, 255), default=raw("'it''s'")),
        Column("price", "DECIMAL(10, 2)"),
    ]


def test_columns_keep_unknown_declared_types_verbatim(db: Database) -> None:
    db.exec("CREATE TABLE loose (a, b Json, c VARCHAR, d INTEGER PRIMARY KEY)")

    assert db.information_schema_columns("loose") == [
        Column("a", ""),
        Column("b", "Json"),
        Column("c", Type(TypeEnum.VARCHAR)),
        Column("d", Type(TypeEnum.INTEGER, 32)),
    ]


def test_primary_keys_in_key_order(db: Database) -> None:
    db.exec("CREATE TABLE reversed (a INTEGER, b INTEGER, c INTEGER, PRIMARY KEY (c, a))")

    assert db.information_schema_primary_keys("authors") == ["id"]
    assert db.information_schema_primary_keys("books") == ["isbn", "edition"]
    assert db.information_schema_primary_keys("reversed") == ["c", "a"]


def test_unique_constraints_are_unnamed_and_exclude_unique_indexes(db: Database) -> None:
    assert db.information_schema_unique_constraints("authors") == [UniqueConstraint(["name"])]
    assert db.information_schema_unique_constraints("books") == [UniqueConstraint(["title", "author_id"])]


def test_foreign_key_constraints(db: Database) -> None:
    assert db.information_schema_foreign_key_constraints("books") == [
        ForeignKeyConstraint(["author_id"], "authors", ["id"], None, CASCADE, RESTRICT),
        ForeignKeyConstraint(["reviewer_id"], "authors", ["id"], None, NO_ACTION, SET_NULL),
    ]
    assert db.information_schema_foreign_key_constraints("authors") == []


def test_foreign_keys_without_reference_columns_reference_the_primary_key(db: Database) -> None:
    db.exec("CREATE TABLE editions (isbn TEXT, edition INTEGER, FOREIGN KEY (isbn, edition) REFERENCES books)")

    assert db.information_schema_foreign_key_constraints("editions") == [
        ForeignKeyConstraint(["isbn", "edition"], "books", ["isbn", "edition"], None, NO_ACTION, NO_ACTION)
    ]


def test_indexes_skip_the_primary_key_and_expression_indexes(db: Database) -> None:
    indexes = db.information_schema_indexes("books")

    assert indexes[:3] == [
        Index("books_author_edition", ["author_id", "edition"], True),
        Index("books_recent", ["title"], False),
        Index("books_title", ["title"], False),
    ]
    assert [
        (index.name.startswith("sqlite_autoindex_books_"), index.columns, index.unique) for index in indexes[3:]
    ] == [(True, ["title", "author_id"], True)]


def test_identifiers_are_bound_not_interpolated(db: Database) -> None:
    table, index = "we\"ird' table", 'we\'ird "index"'
    db.create_table(table).integer("a b", 32).execute()
    db.create_index(table, index).columns(["a b"]).execute()

    assert db.information_schema_columns(table) == [Column("a b", Type(TypeEnum.INTEGER, 32))]
    assert db.information_schema_indexes(table) == [Index(index, ["a b"], False)]
    assert db.information_schema_columns("authors') --") == []


@pytest.mark.parametrize("table", ["authors", "books"])
def test_round_trip(db: Database, table: str) -> None:
    copy_table(db, table, f"{table}_copy")

    assert describe(db, f"{table}_copy") == describe(db, table)


def test_round_trip_of_an_integer_primary_key_without_autoincrement(db: Database) -> None:
    db.exec("CREATE TABLE plain (id INTEGER PRIMARY KEY, name TEXT)")
    copy_table(db, "plain", "plain_copy")

    assert db.information_schema_columns("plain_copy") == db.information_schema_columns("plain")
    assert db.query("SELECT sql FROM sqlite_master WHERE name = 'plain_copy'").scalar() == (
        'CREATE TABLE "plain_copy" ("id" INTEGER, "name" TEXT, PRIMARY KEY ("id"))'
    )


def test_emulated_create_table_if_not_exists(db: Database) -> None:
    db.create_table("authors").if_not_exists().emulate_if_not_exists().integer("other").execute()
    db.create_table("tags").if_not_exists().emulate_if_not_exists().integer("id").execute()

    assert [column.name for column in db.information_schema_columns("authors")][:2] == ["id", "name"]
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
    assert db.information_schema_indexes("authors")[0] == Index("authors_bio", ["bio"], False)


def test_emulated_drop_index_if_exists(db: Database) -> None:
    db.drop_index("books", "missing").if_exists().emulate_if_exists().execute()
    db.drop_index("books", "books_title").if_exists().emulate_if_exists().execute()

    assert "books_title" not in [index.name for index in db.information_schema_indexes("books")]


def test_copied_foreign_keys_keep_working(db: Database) -> None:
    db.exec("PRAGMA foreign_keys = ON")
    copy_table(db, "books", "books_copy")
    db.insert("authors").values({"name": "Ann"}).execute()
    db.insert("books_copy").values({"author_id": 1, "isbn": "1", "title": "A"}).execute()
    db.update("authors").set({"id": 2}).execute()

    assert db.query("SELECT author_id FROM books_copy").scalar() == 2
