"""Unix domain socket connection target."""

from __future__ import annotations

from dataclasses import dataclass, field

from .socket_abc import SocketABC


@dataclass(frozen=True)
class UnixSocket(SocketABC):
    """Connect through the unix domain socket at ``path``.

    For PostgreSQL ``path`` is the socket *directory* (e.g. ``/var/run/postgresql``), as libpq expects;
    for MySQL/MariaDB it is the socket file (e.g. ``/var/run/mysqld/mysqld.sock``).
    """

    path: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)
