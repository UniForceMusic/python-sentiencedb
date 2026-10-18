"""Result sets."""

from __future__ import annotations

from .mysql_connector_result import MySQLConnectorResult
from .psycopg_result import PsycopgResult
from .result import Result
from .result_abc import ResultABC
from .sqlite3_result import SQLite3Result

__all__ = ["MySQLConnectorResult", "PsycopgResult", "Result", "ResultABC", "SQLite3Result"]
