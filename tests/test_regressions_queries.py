"""Regressions from the v2 review of the query builders: caller containers, ``str`` column lists and live SQL."""

from __future__ import annotations

from typing import Any

from sentiencedb import Database, raw
from sentiencedb.queries import DeleteQuery, SelectQuery, UpdateQuery


def test_builders_do_not_alias_caller_containers(mock_database: Any, sqlite_dialect: Any) -> None:
    """PHP arrays are copied by value: changing a list or dict after the call must not change the query."""
    ids = [1, 2]
    updates: dict[str, Any] = {"a": 1}
    select = SelectQuery(mock_database, sqlite_dialect, "t").where_in("id", ids).having_not_in("id", ids)
    update = UpdateQuery(mock_database, sqlite_dialect, "t").set(updates)

    ids.append(3)
    updates["b"] = 2

    assert select.to_sql() == 'SELECT * FROM "t" WHERE "id" IN (1, 2) HAVING "id" NOT IN (1, 2)'
    assert update.to_sql() == 'UPDATE "t" SET "a" = 1'


def test_returning_a_str_is_one_column(mock_database: Any, sqlite_dialect: Any) -> None:
    update = UpdateQuery(mock_database, sqlite_dialect, "t").set({"a": 1}).returning("email")
    delete = DeleteQuery(mock_database, sqlite_dialect, "t").returning("email")

    assert update.to_sql().endswith('RETURNING "email"')
    assert delete.to_sql().endswith('RETURNING "email"')


def test_live_str_column_lists() -> None:
    with Database.connect_sqlite() as db:
        db.create_table("t").varchar("email").unique_constraint("email").execute()
        db.create_index("t", "by_email").columns("email").execute()
        db.insert("t").values({"email": "a"}, {"email": "b"}).execute()

        assert db.select("t").columns("email").group_by("email").count() == 2
        assert db.table("t").unique_constraints()[0].columns == ["email"]
        assert [index.columns for index in db.table("t").indexes() if index.name == "by_email"] == [["email"]]


def test_live_count_keeps_aggregates_and_limits() -> None:
    """Replacing the select list with ``1`` must not turn an aggregate (one row) into one row per input row."""
    with Database.connect_sqlite() as db:
        db.create_table("t").integer("n").execute()
        db.insert("t").values({"n": 1}, {"n": 2}, {"n": 2}).execute()

        assert db.select("t").columns([raw("max(n)")]).count() == 1
        assert db.select("t").columns({"m": "n"}).order_by_desc("m").limit(2).count() == 2
        assert db.select("t").columns(["n"]).distinct().count() == 2
        assert db.select("t").group_by("n").count() == 2
