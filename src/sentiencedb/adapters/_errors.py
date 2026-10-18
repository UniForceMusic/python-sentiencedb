"""Mapping of driver exceptions to the sentiencedb exception hierarchy (the original becomes ``__cause__``)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from types import ModuleType

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
    1263: NotNullViolationError,  # ER_WARN_NULL_TO_NOTNULL (strict mode)
    1364: NotNullViolationError,  # ER_NO_DEFAULT_FOR_FIELD: NOT NULL column without default omitted (strict mode)
    3819: CheckViolationError,  # ER_CHECK_CONSTRAINT_VIOLATED (MySQL raises it as a plain DatabaseError)
    4025: CheckViolationError,  # ER_CONSTRAINT_FAILED (MariaDB)
}

_MYSQL_CLASSES: Mapping[int, type[DriverError]] = {
    1292: DataError,  # ER_TRUNCATED_WRONG_VALUE (incorrect date/time value)
    1366: DataError,  # ER_TRUNCATED_WRONG_VALUE_FOR_FIELD (incorrect integer value); MySQL raises a DatabaseError
}

_SQLSTATE_CLASSES: Mapping[str, type[DriverError]] = {
    "22": DataError,
    "23": IntegrityError,
    "42": ProgrammingError,
}
"""Fallback by SQLSTATE class for errors the driver raises as its generic ``DatabaseError``."""


def map_sqlite3_error(error: BaseException, query: str | None, *, connecting: bool = False) -> DriverError | None:
    """Map a ``sqlite3`` exception (``None`` for anything else); ``code`` becomes its extended result code."""
    if not isinstance(error, sqlite3.Error):
        return None

    code: int | None = getattr(error, "sqlite_errorcode", None)
    violation = None if code is None else _SQLITE3_VIOLATIONS.get(code)

    return _map(sqlite3, error, query, connecting, code=code, mapped=violation)


def map_psycopg_error(error: BaseException, query: str | None, *, connecting: bool = False) -> DriverError | None:
    """Map a psycopg exception (``None`` for anything else); ``sqlstate`` becomes its SQLSTATE."""
    try:
        import psycopg
    except ImportError:
        return None

    if not isinstance(error, psycopg.Error):
        return None

    sqlstate = error.sqlstate
    violation = None if sqlstate is None else _PSYCOPG_VIOLATIONS.get(sqlstate)

    return _map(psycopg, error, query, connecting, sqlstate=sqlstate, mapped=violation)


def map_mysql_connector_error(
    error: BaseException, query: str | None, *, connecting: bool = False
) -> DriverError | None:
    """Map a mysql-connector exception (``None`` for anything else); ``sqlstate`` and ``code`` become its SQLSTATE
    and MySQL error number."""
    try:
        import mysql.connector
    except ImportError:
        return None

    if not isinstance(error, mysql.connector.Error):
        return None

    errno = error.errno
    mapped = _MYSQL_VIOLATIONS.get(errno) or _MYSQL_CLASSES.get(errno)

    return _map(mysql.connector, error, query, connecting, sqlstate=error.sqlstate, code=errno, mapped=mapped)


def _map(
    module: ModuleType,
    error: Exception,
    query: str | None,
    connecting: bool,
    *,
    sqlstate: str | None = None,
    code: int | None = None,
    mapped: type[DriverError] | None = None,
) -> DriverError:
    """Pick the class the error code identifies (``mapped``), else the driver's PEP 249 exception class (drivers do
    not always raise violations as their ``IntegrityError``), else -- for the driver's generic ``DatabaseError`` --
    the class of the SQLSTATE."""
    cls: type[DriverError]

    if connecting:
        cls = ConnectionFailedError
    elif mapped is not None:
        cls = mapped
    else:
        cls = next(
            (pep_class for name, pep_class in _PEP_249_CLASSES if isinstance(error, getattr(module, name))),
            DriverError,
        )

        if cls is DriverError and sqlstate is not None and isinstance(error, module.DatabaseError):
            cls = _SQLSTATE_CLASSES.get(sqlstate[:2], DriverError)

    return cls(str(error), query=query, sqlstate=sqlstate, code=code)


__all__ = ["map_mysql_connector_error", "map_psycopg_error", "map_sqlite3_error"]
