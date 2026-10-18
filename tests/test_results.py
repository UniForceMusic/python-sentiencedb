from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sentiencedb import Result


def make_result() -> Result:
    return Result({"id": "INTEGER", "name": "TEXT"}, [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])


class User:
    def __init__(self, role: str, *, active: bool = False) -> None:
        self.role = role
        self.active = active
        self.id: int | None = None
        self.name: str | None = None


def test_columns() -> None:
    assert make_result().columns() == {"id": "INTEGER", "name": "TEXT"}


def test_fetch_dict_is_single_pass() -> None:
    result = make_result()

    assert result.fetch_dict() == {"id": 1, "name": "Alice"}
    assert len(result) == 1
    assert result.fetch_dicts() == [{"id": 2, "name": "Bob"}]
    assert result.fetch_dict() is None
    assert result.fetch_dicts() == []


def test_rows_are_copied() -> None:
    rows: list[dict[str, Any]] = [{"id": 1}]
    result = Result({"id": None}, rows)
    rows[0]["id"] = 2

    assert result.fetch_dicts() == [{"id": 1}]


def test_scalar() -> None:
    result = make_result()

    assert result.scalar() == 1
    assert result.scalar("name") == "Bob"
    assert result.scalar() is None


def test_scalar_of_unknown_column_is_none() -> None:
    assert make_result().scalar("missing") is None


def test_fetch_object_defaults_to_simple_namespace() -> None:
    result = make_result()

    assert result.fetch_object() == SimpleNamespace(id=1, name="Alice")
    assert result.fetch_objects() == [SimpleNamespace(id=2, name="Bob")]
    assert result.fetch_object() is None


def test_fetch_object_with_class_and_constructor_args() -> None:
    result = make_result()

    user = result.fetch_object(User, "admin", active=True)

    assert isinstance(user, User)
    assert (user.id, user.name, user.role, user.active) == (1, "Alice", "admin", True)

    users = result.fetch_objects(User, "guest")

    assert [(user.id, user.role) for user in users] == [(2, "guest")]


def test_iteration_yields_remaining_rows() -> None:
    result = make_result()
    result.fetch_dict()

    assert list(result) == [{"id": 2, "name": "Bob"}]


def test_from_result_snapshots_another_result() -> None:
    source = make_result()
    source.fetch_dict()

    snapshot = Result.from_result(source)

    assert snapshot.columns() == {"id": "INTEGER", "name": "TEXT"}
    assert snapshot.fetch_dicts() == [{"id": 2, "name": "Bob"}]
    assert source.fetch_dict() is None


def test_empty_result() -> None:
    result = Result()

    assert result.columns() == {}
    assert result.fetch_dict() is None
    assert result.scalar() is None
