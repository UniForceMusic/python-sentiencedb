"""``IF NOT EXISTS`` (PHP ``IfNotExistsTrait`` and ``EmulateIfNotExistsTrait``)."""

from __future__ import annotations

from typing import Self


class IfNotExistsMixin:
    """``IF NOT EXISTS`` for ``CREATE`` statements, natively or emulated through introspection."""

    _if_not_exists = False
    _emulate_if_not_exists = False

    def if_not_exists(self) -> Self:
        """Do nothing when the object already exists."""
        self._if_not_exists = True

        return self

    def emulate_if_not_exists(self) -> Self:
        """Check :meth:`if_not_exists` through introspection instead of ``IF NOT EXISTS``, even when the server
        supports it."""
        self._emulate_if_not_exists = True

        return self


__all__ = ["IfNotExistsMixin"]
