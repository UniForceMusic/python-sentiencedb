"""``RETURNING`` clause (PHP ``ReturningTrait``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from ..helpers import column_names


class ReturningMixin:
    """``RETURNING`` clause of ``INSERT``, ``UPDATE`` and ``DELETE``."""

    _returning: tuple[str, ...] | None = None

    def returning(self, columns: str | Sequence[str] = ()) -> Self:
        """Return the affected rows' ``columns`` (a ``str`` is one column); no columns returns every column
        (``RETURNING *``)."""
        self._returning = column_names(columns)

        return self


__all__ = ["ReturningMixin"]
