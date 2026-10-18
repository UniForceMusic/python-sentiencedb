"""Package level guarantees: public API, optional drivers, exceptions and sockets."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

import sentiencedb
from sentiencedb import exceptions
from sentiencedb.sockets import NetworkSocket, UnixSocket


def test_version() -> None:
    assert sentiencedb.__version__ == "2.0.0"


def test_every_public_name_is_importable() -> None:
    for name in sentiencedb.__all__:
        assert hasattr(sentiencedb, name), name


def test_imports_without_optional_drivers() -> None:
    script = textwrap.dedent(
        """
        import sys

        class BlockDrivers:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in ("psycopg", "psycopg_binary", "mysql"):
                    raise ImportError(f"blocked {name}")
                return None

        sys.meta_path.insert(0, BlockDrivers())

        import sentiencedb
        import sentiencedb.adapters
        import sentiencedb.dialects
        import sentiencedb.queries
        import sentiencedb.results
        import sentiencedb.schemas
        from sentiencedb import Database, Driver, HavingGroup, Join, SelectQuery, WhereGroup, raw
        """
    )

    subprocess.run([sys.executable, "-c", script], check=True)


@pytest.mark.parametrize(
    "name",
    [
        "ConnectionFailedError",
        "OperationalError",
        "ProgrammingError",
        "DataError",
        "NotSupportedError",
        "InternalError",
        "IntegrityError",
    ],
)
def test_driver_errors_derive_from_driver_error(name: str) -> None:
    assert issubclass(getattr(exceptions, name), exceptions.DriverError)


@pytest.mark.parametrize(
    "name", ["UniqueViolationError", "ForeignKeyViolationError", "NotNullViolationError", "CheckViolationError"]
)
def test_constraint_violations_derive_from_integrity_error(name: str) -> None:
    assert issubclass(getattr(exceptions, name), exceptions.IntegrityError)


def test_exception_hierarchy() -> None:
    assert issubclass(exceptions.ConnectionLimitError, exceptions.AdapterError)
    assert issubclass(exceptions.QueryWithParamsError, exceptions.QueryError)

    for name in exceptions.__all__:
        assert issubclass(getattr(exceptions, name), exceptions.DatabaseError)


def test_driver_error_keeps_details_and_cause() -> None:
    original = ValueError("duplicate key")

    try:
        raise exceptions.UniqueViolationError(
            str(original), query="INSERT ..", sqlstate="23505", code=None
        ) from original
    except exceptions.DriverError as error:
        assert str(error) == "duplicate key"
        assert (error.query, error.sqlstate, error.code) == ("INSERT ..", "23505", None)
        assert error.__cause__ is original


def test_sockets_hide_passwords_from_repr() -> None:
    network = NetworkSocket("localhost", 5432, "postgres", "secret")
    unix = UnixSocket("/var/run/mysqld/mysqld.sock", "root", "secret")

    assert network.host == "localhost"
    assert network.port == 5432
    assert "secret" not in repr(network)
    assert unix.path == "/var/run/mysqld/mysqld.sock"
    assert "secret" not in repr(unix)
