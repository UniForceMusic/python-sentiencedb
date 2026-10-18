"""``IF EXISTS`` (PHP ``IfExistsTrait`` and ``EmulateIfExistsTrait``)."""

from __future__ import annotations

from typing import Self


class IfExistsMixin:
    """``IF EXISTS`` for ``DROP`` statements, natively or emulated through introspection."""

    _if_exists = False
    _emulate_if_exists = False

    def if_exists(self) -> Self:
        """Do nothing when the object does not exist."""
        self._if_exists = True

        return self

    def emulate_if_exists(self) -> Self:
        """Check :meth:`if_exists` through introspection instead of ``IF EXISTS``, even when the server supports it."""
        self._emulate_if_exists = True

        return self


__all__ = ["IfExistsMixin"]
