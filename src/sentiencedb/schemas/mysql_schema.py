"""MySQL / MariaDB introspection."""

from __future__ import annotations

from .sql_schema import SQLSchema


class MySQLSchema(SQLSchema):
    """MySQL / MariaDB introspection.

    Phase 3 stub: see DESIGN.md.
    """


__all__ = ["MySQLSchema"]
