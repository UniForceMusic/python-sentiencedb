"""Connection targets for server databases."""

from __future__ import annotations

from .network_socket import NetworkSocket
from .socket_abc import SocketABC
from .unix_socket import UnixSocket

__all__ = ["NetworkSocket", "SocketABC", "UnixSocket"]
