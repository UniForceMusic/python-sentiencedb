"""Mapping of driver exceptions to the sentiencedb exception hierarchy (the original becomes ``__cause__``)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from types import ModuleType
from typing import TYPE_CHECKING

from ..exceptions import (
    CheckViolationError,
    ConnectionFailedError,
    DataError,
    DriverError,
    ForeignKeyViolationError,
    IntegrityError,
    InternalError,
    NotNullViolationError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
    UniqueViolationError,
)

if TYPE_CHECKING:
    import mysql.connector
    import psycopg

_PEP_249_CLASSES: tuple[tuple[str, type[DriverError]], ...] = (
    ("IntegrityError", IntegrityError),
    ("OperationalError", OperationalError),
    ("ProgrammingError", ProgrammingError),
    ("DataError", DataError),
    ("NotSupportedError", NotSupportedError),
    ("InternalError", InternalError),
)

_SQLITE3_VIOLATIONS: Mapping[int, type[IntegrityError]] = {
    2067: UniqueViolationError,  # SQLITE_CONSTRAINT_UNIQUE
    1555: UniqueViolationError,  # SQLITE_CONSTRAINT_PRIMARYKEY
    787: ForeignKeyViolationError,  # SQLITE_CONSTRAINT_FOREIGNKEY
    1299: NotNullViolationError,  # SQLITE_CONSTRAINT_NOTNULL
    275: CheckViolationError,  # SQLITE_CONSTRAINT_CHECK
}

_PSYCOPG_VIOLATIONS: Mapping[str, type[IntegrityError]] = {
    "23505": UniqueViolationError,
    "23503": ForeignKeyViolationError,
    "23502": NotNullViolationError,
    "23514": CheckViolationError,
}

_MYSQL_VIOLATIONS: Mapping[int, type[IntegrityError]] = {
    1062: UniqueViolationError,  # ER_DUP_ENTRY
    1586: UniqueViolationError,  # ER_DUP_ENTRY_WITH_KEY_NAME
    1216: ForeignKeyViolationError,  # ER_NO_REFERENCED_ROW
    1217: ForeignKeyViolationError,  # ER_ROW_IS_REFERENCED
    1451: ForeignKeyViolationError,  # ER_ROW_IS_REFERENCED_2
    1452: ForeignKeyViolationError,  # ER_NO_REFERENCED_ROW_2
    1048: NotNullViolationError,  # ER_BAD_NULL_ERROR
    3819: CheckViolationError,  # ER_CHECK_CONSTRAINT_VIOLATED
}


def map_sqlite3_error(error: sqlite3.Error, query: str | None, *, connecting: bool = False) -> DriverError:
    """Map a ``sqlite3`` exception; ``code`` becomes its extended result code."""
    code: int | None = getattr(error, "sqlite_errorcode", None)
    violation = None if code is None else _SQLITE3_VIOLATIONS.get(code)

    return _map(sqlite3, error, query, connecting, code=code, violation=violation)


def map_psycopg_error(error: psycopg.Error, query: str | None, *, connecting: bool = False) -> DriverError:
    """Map a psycopg exception; ``sqlstate`` becomes its SQLSTATE."""
    import psycopg

    sqlstate = error.sqlstate
    violation = None if sqlstate is None else _PSYCOPG_VIOLATIONS.get(sqlstate)

    return _map(psycopg, error, query, connecting, sqlstate=sqlstate, violation=violation)


def map_mysql_connector_error(
    error: mysql.connector.Error, query: str | None, *, connecting: bool = False
) -> DriverError:
    """Map a mysql-connector exception; ``sqlstate`` and ``code`` become its SQLSTATE and MySQL error number."""
    import mysql.connector

    violation = _MYSQL_VIOLATIONS.get(error.errno)

    return _map(
        mysql.connector, error, query, connecting, sqlstate=error.sqlstate, code=error.errno, violation=violation
    )


def _map(
    module: ModuleType,
    error: Exception,
    query: str | None,
    connecting: bool,
    *,
    sqlstate: str | None = None,
    code: int | None = None,
    violation: type[IntegrityError] | None = None,
) -> DriverError:
    """Pick the class by the driver's PEP 249 exception class, refined by the constraint ``violation``."""
    if connecting:
        mapped: type[DriverError] = ConnectionFailedError
    else:
        mapped = next((cls for name, cls in _PEP_249_CLASSES if isinstance(error, getattr(module, name))), DriverError)

        if mapped is IntegrityError and violation is not None:
            mapped = violation

    return mapped(str(error), query=query, sqlstate=sqlstate, code=code)


__all__ = ["map_mysql_connector_error", "map_psycopg_error", "map_sqlite3_error"]
