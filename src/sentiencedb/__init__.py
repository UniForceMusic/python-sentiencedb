"""sentiencedb: a database abstraction layer for SQLite, PostgreSQL, MySQL and MariaDB.

A Python port of the PHP package ``sentience/database``::

    from sentiencedb import Database

    db = Database.connect_sqlite(":memory:")
    rows = db.select("users").where_equals("name", "Alice").execute().fetch_dicts()
"""

from __future__ import annotations

from .database import Database
from .database_abc import DatabaseABC
from .driver import Driver
from .exceptions import (
    AdapterError,
    CheckViolationError,
    ConnectionFailedError,
    ConnectionLimitError,
    DatabaseError,
    DataError,
    DriverError,
    ForeignKeyViolationError,
    IntegrityError,
    InternalError,
    NotNullViolationError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
    QueryError,
    QueryWithParamsError,
    UniqueViolationError,
)
from .options import DebugCallback, Options
from .queries.enums import (
    ChainEnum,
    ConditionEnum,
    JoinEnum,
    OrderByDirectionEnum,
    ReferentialActionEnum,
    TypeEnum,
    UnionEnum,
)
from .queries.helpers import (
    alias,
    current_timestamp,
    escape_ansi,
    escape_backslash,
    expression,
    expressionf,
    identifier,
    now,
    raw,
    sub_query,
)
from .queries.objects.query_with_params import QueryWithParams
from .results.result import Result
from .results.result_abc import ResultABC
from .sockets import NetworkSocket, SocketABC, UnixSocket

__version__ = "2.0.0"

__all__ = [
    "AdapterError",
    "ChainEnum",
    "CheckViolationError",
    "ConditionEnum",
    "ConnectionFailedError",
    "ConnectionLimitError",
    "DataError",
    "Database",
    "DatabaseABC",
    "DatabaseError",
    "DebugCallback",
    "Driver",
    "DriverError",
    "ForeignKeyViolationError",
    "IntegrityError",
    "InternalError",
    "JoinEnum",
    "NetworkSocket",
    "NotNullViolationError",
    "NotSupportedError",
    "OperationalError",
    "Options",
    "OrderByDirectionEnum",
    "ProgrammingError",
    "QueryError",
    "QueryWithParams",
    "QueryWithParamsError",
    "ReferentialActionEnum",
    "Result",
    "ResultABC",
    "SocketABC",
    "TypeEnum",
    "UnionEnum",
    "UniqueViolationError",
    "UnixSocket",
    "__version__",
    "alias",
    "current_timestamp",
    "escape_ansi",
    "escape_backslash",
    "expression",
    "expressionf",
    "identifier",
    "now",
    "raw",
    "sub_query",
]
