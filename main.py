"""sentiencedb example: CRUD, a join, an upsert and a transaction.

Runs against an in-memory SQLite database by default::

    .venv/bin/python main.py

Pass flags to also run the same demo against the servers from docker-compose.yml (``docker compose up -d``)::

    .venv/bin/python main.py --postgresql --mysql --mariadb

or set ``SENTIENCEDB_DEMO=postgresql,mysql,mariadb``. Add ``--debug`` to print every statement.
"""

from __future__ import annotations

import argparse
import os
import time

from sentiencedb import (
    Database,
    DebugCallback,
    ReferentialActionEnum,
    UniqueViolationError,
    alias,
    current_timestamp,
    expressionf,
    identifier,
    raw,
)

SERVERS = ("postgresql", "mysql", "mariadb")


def print_query(query: str, start: float, error: str | None) -> None:
    """Debug callback: print each statement with its duration and error."""
    suffix = f"  -- failed: {error}" if error else ""
    print(f"    [{(time.time() - start) * 1000:6.2f} ms] {query}{suffix}")


def demo(db: Database) -> None:
    """Create two tables, run CRUD, a join, an upsert and a transaction, then drop the tables again."""
    print(f"== {db.dialect.driver} (server version {db.dialect.version})")

    db.drop_table("demo_posts").if_exists().execute()
    db.drop_table("demo_users").if_exists().execute()

    (
        db.create_table("demo_users")
        .identity("id")
        .varchar("name", 100, not_null=True)
        .varchar("email", not_null=True)
        .integer("logins", not_null=True, default=0)
        .datetime("created_at", default=current_timestamp())
        .unique_constraint(["email"], name="uq_demo_users_email")
        .execute()
    )
    (
        db.create_table("demo_posts")
        .identity("id")
        .integer("user_id", not_null=True)
        .varchar("title", not_null=True)
        .foreign_key_constraint("user_id", "demo_users", "id", on_delete=ReferentialActionEnum.CASCADE)
        .execute()
    )

    # Create: RETURNING runs natively where supported and is emulated through the id column elsewhere.
    alice = (
        db.insert("demo_users")
        .values({"name": "Alice", "email": "alice@example.com"})
        .returning(["id", "name"])
        .last_insert_id("id")
        .execute()
        .fetch_dict()
    )
    print("inserted:", alice)
    db.insert("demo_users").values({"name": "Bob", "email": "bob@example.com"}).execute()
    bob_id = db.last_insert_id()

    db.insert("demo_posts").values(
        {"user_id": 1, "title": "Hello world"},
        {"user_id": 1, "title": "Second post"},
        {"user_id": bob_id, "title": "Hi from Bob"},
    ).execute()

    # Read.
    users = db.select("demo_users").columns(["id", "name"]).order_by_asc("id").execute().fetch_dicts()
    print("users:", users)
    print("posts mentioning 'post':", db.select("demo_posts").where_contains("title", "post").count())

    # Update and delete.
    db.update("demo_posts").set({"title": "Hello, world"}).where_equals("title", "Hello world").execute()
    db.delete("demo_posts").where_equals("title", "Second post").execute()

    # A join with an aggregate.
    per_user = (
        db.select_table("demo_users", "u")
        .columns([["u", "name"], alias(raw("count(p.id)"), "posts")])
        .left_join_table("demo_posts", lambda join: join.on(["p", "user_id"], ["u", "id"]), alias="p")
        .group_by([["u", "name"]])
        .order_by_asc(["u", "name"])
    )
    print("posts per user:", per_user.execute().fetch_dicts())

    # Upsert: count a login, inserting the user when the email is new.
    for email, name in [("alice@example.com", "Alice"), ("carol@example.com", "Carol")]:
        (
            db.insert("demo_users")
            .values({"name": name, "email": email, "logins": 1})
            .on_conflict_do_update(["email"], {"logins": expressionf("%s + 1", identifier(["demo_users", "logins"]))})
            .execute()
        )
    logins = db.select("demo_users").columns(["name", "logins"]).order_by_asc("id").execute().fetch_dicts()
    print("logins after upserts:", logins)

    # Transaction: everything inside the callback is rolled back when it raises.
    def duplicate_bob(tx: Database) -> None:
        tx.insert("demo_posts").values({"user_id": bob_id, "title": "Never saved"}).execute()
        tx.insert("demo_users").values({"name": "Bob again", "email": "bob@example.com"}).execute()

    try:
        db.transaction(duplicate_bob)
    except UniqueViolationError as error:
        print(f"transaction rolled back ({type(error).__name__})")
    print("posts after rollback:", db.select("demo_posts").count())

    db.drop_table("demo_posts").execute()
    db.drop_table("demo_users").execute()


def connect(server: str, debug: DebugCallback | None) -> Database:
    """Connect to one of the docker-compose.yml servers."""
    match server:
        case "postgresql":
            return Database.connect_postgresql("postgres", username="postgres", debug=debug)
        case "mysql" | "mariadb":
            port = 3306 if server == "mysql" else 3307
            setup = Database.connect_mysql("mysql", host="127.0.0.1", port=port, username="root")
            setup.exec("CREATE DATABASE IF NOT EXISTS sentiencedb")
            setup.close()
            helper = Database.connect_mysql if server == "mysql" else Database.connect_mariadb
            return helper("sentiencedb", host="127.0.0.1", port=port, username="root", debug=debug)
        case _:
            raise ValueError(f"unknown server {server!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for server in SERVERS:
        parser.add_argument(f"--{server}", action="store_true", help=f"also run the demo on {server}")
    parser.add_argument("--debug", action="store_true", help="print every statement")
    args = parser.parse_args()

    debug = print_query if args.debug else None
    from_env = {name.strip().lower() for name in os.environ.get("SENTIENCEDB_DEMO", "").split(",") if name.strip()}

    with Database.connect_sqlite(":memory:", options={"foreign_keys": True}, debug=debug) as db:
        demo(db)

    for server in SERVERS:
        if getattr(args, server) or server in from_env:
            with connect(server, debug) as db:
                demo(db)


if __name__ == "__main__":
    main()
