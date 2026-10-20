"""sentiencedb: a database abstraction layer for SQLite, PostgreSQL, MySQL and MariaDB.

A Python port of the PHP package ``sentience/database``::

    from sentiencedb import connect_sqlite

    db = connect_sqlite(":memory:")
    rows = db.select("users").where_equals("name", "Alice").execute().fetch_dicts()
"""

from __future__ import annotations

from importlib.metadata import version as _version

from .connection import connect, connect_mariadb, connect_mysql, connect_postgresql, connect_sqlite
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
from .queries import (
    AlterTableQuery,
    CreateIndexQuery,
    CreateTableQuery,
    DeleteQuery,
    DropIndexQuery,
    DropTableQuery,
    HavingGroup,
    InsertQuery,
    Join,
    Query,
    QueryFactory,
    SelectQuery,
    UpdateQuery,
    WhereGroup,
)
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

__version__ = _version("sentiencedb")

__all__ = [
    "AdapterError",
    "AlterTableQuery",
    "ChainEnum",
    "CheckViolationError",
    "ConditionEnum",
    "ConnectionFailedError",
    "ConnectionLimitError",
    "CreateIndexQuery",
    "CreateTableQuery",
    "DataError",
    "Database",
    "DatabaseABC",
    "DatabaseError",
    "DebugCallback",
    "DeleteQuery",
    "Driver",
    "DriverError",
    "DropIndexQuery",
    "DropTableQuery",
    "ForeignKeyViolationError",
    "HavingGroup",
    "InsertQuery",
    "IntegrityError",
    "InternalError",
    "Join",
    "JoinEnum",
    "NetworkSocket",
    "NotNullViolationError",
    "NotSupportedError",
    "OperationalError",
    "Options",
    "OrderByDirectionEnum",
    "ProgrammingError",
    "Query",
    "QueryError",
    "QueryFactory",
    "QueryWithParams",
    "QueryWithParamsError",
    "ReferentialActionEnum",
    "Result",
    "ResultABC",
    "SelectQuery",
    "SocketABC",
    "TypeEnum",
    "UnionEnum",
    "UniqueViolationError",
    "UnixSocket",
    "UpdateQuery",
    "WhereGroup",
    "__version__",
    "alias",
    "connect",
    "connect_mariadb",
    "connect_mysql",
    "connect_postgresql",
    "connect_sqlite",
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
