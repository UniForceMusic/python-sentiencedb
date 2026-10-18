"""Driver exceptions are mapped to the sentiencedb hierarchy with the original kept as ``__cause__``."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from sentiencedb import Database
from sentiencedb.adapters._errors import map_mysql_connector_error, map_psycopg_error, map_sqlite3_error
from sentiencedb.exceptions import (
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
    QueryWithParamsError,
    UniqueViolationError,
)


@pytest.fixture
def db() -> Iterator[Database]:
    with Database.connect_sqlite(options={"foreign_keys": True}) as db:
        db.exec("CREATE TABLE parents (id INTEGER PRIMARY KEY)")
        db.exec(
            "CREATE TABLE children (id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT NOT NULL CHECK (name <> ''),"
            " parent_id INTEGER REFERENCES parents (id))"
        )
        db.exec("INSERT INTO children (id, code, name) VALUES (1, 'a', 'Alice')")
        yield db


@pytest.mark.parametrize(
    ("sql", "expected", "code"),
    [
        ("INSERT INTO children (code, name) VALUES ('a', 'Bob')", UniqueViolationError, 2067),
        ("INSERT INTO children (id, name) VALUES (1, 'Bob')", UniqueViolationError, 1555),
        ("INSERT INTO children (name, parent_id) VALUES ('Bob', 7)", ForeignKeyViolationError, 787),
        ("INSERT INTO children (name) VALUES (NULL)", NotNullViolationError, 1299),
        ("INSERT INTO children (name) VALUES ('')", CheckViolationError, 275),
    ],
)
def test_sqlite3_constraint_violations(db: Database, sql: str, expected: type[IntegrityError], code: int) -> None:
    with pytest.raises(expected) as error:
        db.exec(sql)

    assert (error.value.query, error.value.code, error.value.sqlstate) == (sql, code, None)
    assert isinstance(error.value.__cause__, sqlite3.IntegrityError)


def test_sqlite3_errors_keep_the_pep_249_class(db: Database) -> None:
    with pytest.raises(OperationalError) as error:
        db.query("SELECT * FROM missing")

    assert str(error.value) == "no such table: missing"
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)


def test_parametrised_statement_errors_are_mapped(db: Database) -> None:
    with pytest.raises(UniqueViolationError) as error:
        db.prepared("INSERT INTO children (code, name) VALUES (?, ?)", ["a", "Bob"])

    assert error.value.query == "INSERT INTO children (code, name) VALUES (?, ?)"


def test_errors_computing_later_rows_are_raised_by_the_statement(db: Database) -> None:
    db.exec("INSERT INTO children (id, name) VALUES (2, 'Bob')")
    sql = "SELECT regexp_like(name, CASE id WHEN 1 THEN 'A' ELSE '(' END) FROM children ORDER BY id"

    with pytest.raises(OperationalError) as error:
        db.query(sql)

    assert error.value.query == sql


def test_own_exceptions_are_not_wrapped(db: Database) -> None:
    with pytest.raises(QueryWithParamsError):
        db.prepared("SELECT ?, ?", [1])


def test_debug_callback_exceptions_become_warnings() -> None:
    def debug(query: str, start: float, error: str | None) -> None:
        raise ValueError(query)

    with Database.connect_sqlite(debug=debug) as db:
        with pytest.warns(RuntimeWarning, match="SELECT 1"):
            assert db.query("SELECT 1 AS one").scalar() == 1

        with pytest.warns(RuntimeWarning), pytest.raises(OperationalError):
            db.exec("SELECT * FROM missing")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (sqlite3.IntegrityError("x"), IntegrityError),
        (sqlite3.OperationalError("x"), OperationalError),
        (sqlite3.ProgrammingError("x"), ProgrammingError),
        (sqlite3.DataError("x"), DataError),
        (sqlite3.NotSupportedError("x"), NotSupportedError),
        (sqlite3.InternalError("x"), InternalError),
        (sqlite3.InterfaceError("x"), DriverError),
        (sqlite3.DatabaseError("x"), DriverError),
    ],
)
def test_sqlite3_pep_249_classes(error: sqlite3.Error, expected: type[DriverError]) -> None:
    mapped = map_sqlite3_error(error, "SELECT 1")

    assert type(mapped) is expected
    assert (str(mapped), mapped.query, mapped.code) == ("x", "SELECT 1", None)


def test_errors_while_connecting_become_connection_failed() -> None:
    mapped = map_sqlite3_error(sqlite3.IntegrityError("x"), None, connecting=True)

    assert type(mapped) is ConnectionFailedError


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("UniqueViolation", UniqueViolationError),
        ("ForeignKeyViolation", ForeignKeyViolationError),
        ("NotNullViolation", NotNullViolationError),
        ("CheckViolation", CheckViolationError),
        ("ExclusionViolation", IntegrityError),
        ("UndefinedTable", ProgrammingError),
        ("DivisionByZero", DataError),
        ("QueryCanceled", OperationalError),
        ("FeatureNotSupported", NotSupportedError),
        ("InternalError_", InternalError),
    ],
)
def test_psycopg_errors(name: str, expected: type[DriverError]) -> None:
    errors = pytest.importorskip("psycopg.errors")
    error = getattr(errors, name)("message")

    mapped = map_psycopg_error(error, "SELECT 1")

    assert type(mapped) is expected
    assert (str(mapped), mapped.query, mapped.sqlstate, mapped.code) == ("message", "SELECT 1", error.sqlstate, None)


def test_psycopg_interface_error() -> None:
    psycopg = pytest.importorskip("psycopg")

    assert type(map_psycopg_error(psycopg.InterfaceError("x"), None)) is DriverError


@pytest.mark.parametrize(
    ("name", "errno", "expected"),
    [
        ("IntegrityError", 1062, UniqueViolationError),
        ("IntegrityError", 1586, UniqueViolationError),
        ("IntegrityError", 1216, ForeignKeyViolationError),
        ("IntegrityError", 1217, ForeignKeyViolationError),
        ("IntegrityError", 1451, ForeignKeyViolationError),
        ("IntegrityError", 1452, ForeignKeyViolationError),
        ("IntegrityError", 1048, NotNullViolationError),
        ("DatabaseError", 1364, NotNullViolationError),
        ("DatabaseError", 1263, NotNullViolationError),
        ("DatabaseError", 1366, DataError),
        ("DataError", 1366, DataError),
        ("DatabaseError", 1292, DataError),
        ("IntegrityError", 3819, CheckViolationError),
        ("DatabaseError", 3819, CheckViolationError),
        ("IntegrityError", 4025, CheckViolationError),
        ("IntegrityError", 1, IntegrityError),
        ("ProgrammingError", 1064, ProgrammingError),
        ("OperationalError", 2013, OperationalError),
        ("DataError", 1264, DataError),
        ("NotSupportedError", 1235, NotSupportedError),
        ("InternalError", 1, InternalError),
        ("InterfaceError", 2003, DriverError),
    ],
)
def test_mysql_connector_errors(name: str, errno: int, expected: type[DriverError]) -> None:
    errors = pytest.importorskip("mysql.connector.errors")
    error = getattr(errors, name)(msg="message", errno=errno, sqlstate="23000")

    mapped = map_mysql_connector_error(error, "SELECT 1")

    assert type(mapped) is expected
    assert (mapped.query, mapped.sqlstate, mapped.code) == ("SELECT 1", "23000", errno)
    assert str(mapped) == str(error)


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("22007", DataError),
        ("23000", IntegrityError),
        ("42S02", ProgrammingError),
        ("HY000", DriverError),
        (None, DriverError),
    ],
)
def test_mysql_generic_errors_fall_back_to_the_sqlstate_class(
    sqlstate: str | None, expected: type[DriverError]
) -> None:
    errors = pytest.importorskip("mysql.connector.errors")

    mapped = map_mysql_connector_error(errors.DatabaseError(msg="message", errno=1, sqlstate=sqlstate), None)

    assert type(mapped) is expected


def test_non_driver_exceptions_are_not_mapped() -> None:
    for mapper in (map_sqlite3_error, map_psycopg_error, map_mysql_connector_error):
        assert mapper(ValueError("x"), None) is None
