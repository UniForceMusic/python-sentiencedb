"""Row handling shared by the schemas."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..queries.enums import ReferentialActionEnum, TypeEnum
from ..queries.objects.ddl import ForeignKeyConstraint, Index, Type

_TYPE = re.compile(r"(?P<name>[^()]*?)\s*(?:\(\s*(?P<size>\d+)\s*\))?(?P<suffix>[^()]*)")


def split_type(sql_type: str) -> tuple[str, int | None] | None:
    """A SQL type as ``(NAME, size)``: ``varchar(255)`` -> ``("VARCHAR", 255)``, ``timestamp(3) without time zone``
    -> ``("TIMESTAMP WITHOUT TIME ZONE", 3)``, ``int unsigned`` -> ``("INT UNSIGNED", None)``. ``None`` for types
    with other arguments (``decimal(10,2)``, ``enum('a')``)."""
    match = _TYPE.fullmatch(sql_type.strip())

    if match is None:
        return None

    name = " ".join(f"{match['name']} {match['suffix']}".upper().split())
    size = match["size"]

    return name, None if size is None else int(size)


def portable_type(name: str, size: int | None) -> Type | None:
    """The portable type of an upper case type name that means the same on every engine, or ``None``."""
    match name:
        case "BOOLEAN":
            return Type(TypeEnum.BOOLEAN)
        case "INTEGER":
            return Type(TypeEnum.INTEGER, 32)
        case "BIGINT":
            return Type(TypeEnum.INTEGER, 64)
        case "REAL":
            return Type(TypeEnum.FLOAT, 64)
        case "VARCHAR":
            return Type(TypeEnum.VARCHAR, size)
        case "TEXT":
            return Type(TypeEnum.TEXT, size)
        case "DATETIME":
            return Type(TypeEnum.DATETIME, size)
        case _:
            return None


def group_rows(rows: Iterable[Mapping[str, Any]], key: str) -> dict[Any, list[Mapping[str, Any]]]:
    """Rows grouped by ``row[key]``, groups and rows in their original order."""
    groups: dict[Any, list[Mapping[str, Any]]] = {}

    for row in rows:
        groups.setdefault(row[key], []).append(row)

    return groups


def foreign_key_constraints(
    rows: Iterable[Mapping[str, Any]], key: str = "constraint_name"
) -> list[ForeignKeyConstraint]:
    """Foreign keys from rows with ``constraint_name``, ``column_name``, ``reference_schema`` (``NULL`` for the
    current schema), ``reference_table``, ``reference_column``, ``update_rule`` and ``delete_rule``, one row per
    column in key order, grouped per constraint by ``row[key]``."""
    return [
        ForeignKeyConstraint(
            [row["column_name"] for row in group],
            _reference_table(group[0]),
            [row["reference_column"] for row in group],
            group[0]["constraint_name"],
            referential_action(group[0]["update_rule"]),
            referential_action(group[0]["delete_rule"]),
        )
        for group in group_rows(rows, key).values()
    ]


def indexes(rows: Iterable[Mapping[str, Any]]) -> list[Index]:
    """Indexes from rows with ``index_name``, ``column_name`` and ``is_unique``, one row per key column in order.
    Indexes on expressions (a ``NULL`` column) are skipped."""
    return [
        Index(name, [row["column_name"] for row in group], bool(group[0]["is_unique"]))
        for name, group in group_rows(rows, "index_name").items()
        if all(row["column_name"] is not None for row in group)
    ]


def referential_action(action: str | None) -> ReferentialActionEnum | str | None:
    """A referential action as its enum member when there is one, else as is."""
    if action is None:
        return None

    try:
        return ReferentialActionEnum(action.upper())
    except ValueError:
        return action


def _reference_table(row: Mapping[str, Any]) -> str | list[str]:
    schema, table = row["reference_schema"], row["reference_table"]

    return table if schema is None else [schema, table]


__all__ = ["foreign_key_constraints", "group_rows", "indexes", "portable_type", "referential_action", "split_type"]
