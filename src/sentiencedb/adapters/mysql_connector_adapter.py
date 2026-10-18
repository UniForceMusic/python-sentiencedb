"""MySQL / MariaDB adapter on mysql-connector-python."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .adapter_abc import AdapterABC

if TYPE_CHECKING:
    from ..dialects.dialect_abc import DialectABC
    from ..driver import Driver
    from ..options import DebugCallback, Options
    from ..queries.objects.query_with_params import QueryWithParams
    from ..results.result_abc import ResultABC
    from ..sockets.socket_abc import SocketABC


class MySQLConnectorAdapter(AdapterABC):
    """MySQL / MariaDB adapter on mysql-connector-python.

    Phase 2 stub (Agent C): see DESIGN.md.
    """

    def __init__(
        self,
        driver: Driver,
        name: str,
        socket: SocketABC | None,
        queries: Sequence[str],
        options: Options,
        debug: DebugCallback | None,
    ) -> None:
        raise NotImplementedError

    def version(self) -> str:
        raise NotImplementedError

    def exec(self, query: str) -> None:
        raise NotImplementedError

    def query(self, query: str) -> ResultABC:
        raise NotImplementedError

    def query_with_params(
        self, dialect: DialectABC, query_with_params: QueryWithParams, emulate_prepare: bool = False
    ) -> ResultABC:
        raise NotImplementedError

    def begin_transaction(self, dialect: DialectABC) -> None:
        raise NotImplementedError

    def commit_transaction(self, dialect: DialectABC) -> None:
        raise NotImplementedError

    def rollback_transaction(self, dialect: DialectABC) -> None:
        raise NotImplementedError

    def begin_savepoint(self, dialect: DialectABC, name: str) -> None:
        raise NotImplementedError

    def commit_savepoint(self, dialect: DialectABC, name: str) -> None:
        raise NotImplementedError

    def rollback_savepoint(self, dialect: DialectABC, name: str) -> None:
        raise NotImplementedError

    def in_transaction(self) -> bool:
        raise NotImplementedError

    def last_insert_id(self, name: str | None = None) -> int | str | None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


__all__ = ["MySQLConnectorAdapter"]
