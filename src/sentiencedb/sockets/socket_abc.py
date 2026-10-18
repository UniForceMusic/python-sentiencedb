"""Base class of connection targets for server databases."""

from __future__ import annotations

from abc import ABC


class SocketABC(ABC):
    """Where (and as whom) to connect to a database server.

    SQLite needs no socket: its database ``name`` is the file path (or ``":memory:"``).
    """

    username: str | None
    password: str | None
