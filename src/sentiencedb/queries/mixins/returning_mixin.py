"""``RETURNING`` clause (PHP ``ReturningTrait``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self


class ReturningMixin:
    """``RETURNING`` clause of ``INSERT``, ``UPDATE`` and ``DELETE``."""

    _returning: tuple[str, ...] | None = None

    def returning(self, columns: Sequence[str] = ()) -> Self:
        """Return the affected rows' ``columns``; no columns returns every column (``RETURNING *``)."""
        self._returning = tuple(columns)

        return self


__all__ = ["ReturningMixin"]
