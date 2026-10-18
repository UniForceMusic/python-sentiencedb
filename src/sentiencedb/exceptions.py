"""Exception hierarchy.

Every exception raised by sentiencedb derives from :class:`DatabaseError`.

* :class:`AdapterError` -- misuse or misconfiguration of an adapter that sentiencedb itself detects
  (missing socket, closed adapter, connection cap reached, ...).
* :class:`QueryError` -- a query cannot be built for the active dialect (unsupported feature, missing values, ...).
* :class:`DriverError` -- an error reported by the underlying driver or database server. The original driver
  exception is always chained as ``__cause__``.
"""

from __future__ import annotations


class DatabaseError(Exception):
    """Base class of every sentiencedb exception."""


class AdapterError(DatabaseError):
    """An adapter was misused or misconfigured."""


class ConnectionLimitError(AdapterError):
    """No connection slot came free within the configured ``acquire_timeout``."""


class QueryError(DatabaseError):
    """A query cannot be built, or uses a feature the dialect does not support."""


class QueryWithParamsError(QueryError):
    """Placeholders and parameters of a :class:`~sentiencedb.queries.objects.QueryWithParams` do not match."""


class DriverError(DatabaseError):
    """An error raised by the database driver or server.

    The driver's own exception is chained as ``__cause__``.

    :param message: Human readable message, normally ``str(original_exception)``.
    :param query: The SQL statement that failed, if any.
    :param sqlstate: Five character SQLSTATE code, when the driver reports one.
    :param code: Driver specific error number (MySQL ``errno``, SQLite extended result code), when available.
    """

    def __init__(
        self,
        message: str,
        *,
        query: str | None = None,
        sqlstate: str | None = None,
        code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.query = query
        self.sqlstate = sqlstate
        self.code = code


class ConnectionFailedError(DriverError):
    """Opening a connection (or running its startup statements) failed."""


class OperationalError(DriverError):
    """PEP 249 ``OperationalError``: lost connection, lock timeout, disk full, ..."""


class ProgrammingError(DriverError):
    """PEP 249 ``ProgrammingError``: SQL syntax error, unknown table or column, ..."""


class DataError(DriverError):
    """PEP 249 ``DataError``: value out of range, invalid input syntax, ..."""


class NotSupportedError(DriverError):
    """PEP 249 ``NotSupportedError``: the server does not support the requested operation."""


class InternalError(DriverError):
    """PEP 249 ``InternalError``: the database encountered an internal error."""


class IntegrityError(DriverError):
    """PEP 249 ``IntegrityError``: a constraint was violated."""


class UniqueViolationError(IntegrityError):
    """A unique or primary key constraint was violated."""


class ForeignKeyViolationError(IntegrityError):
    """A foreign key constraint was violated."""


class NotNullViolationError(IntegrityError):
    """A NOT NULL constraint was violated."""


class CheckViolationError(IntegrityError):
    """A CHECK constraint was violated."""


__all__ = [
    "AdapterError",
    "CheckViolationError",
    "ConnectionFailedError",
    "ConnectionLimitError",
    "DataError",
    "DatabaseError",
    "DriverError",
    "ForeignKeyViolationError",
    "IntegrityError",
    "InternalError",
    "NotNullViolationError",
    "NotSupportedError",
    "OperationalError",
    "ProgrammingError",
    "QueryError",
    "QueryWithParamsError",
    "UniqueViolationError",
]
