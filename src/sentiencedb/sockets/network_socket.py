"""TCP connection target."""

from __future__ import annotations

from dataclasses import dataclass, field

from .socket_abc import SocketABC


@dataclass(frozen=True)
class NetworkSocket(SocketABC):
    """Connect over TCP to ``host:port``."""

    host: str
    port: int
    username: str | None = None
    password: str | None = field(default=None, repr=False)
