"""PostgreSQL introspection."""

from __future__ import annotations

from .sql_schema import SQLSchema


class PgSQLSchema(SQLSchema):
    """PostgreSQL introspection.

    Phase 3 stub: see DESIGN.md.
    """


__all__ = ["PgSQLSchema"]
