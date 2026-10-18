"""The result set interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any, TypeVar, overload

T = TypeVar("T")


class ResultABC(ABC):
    """Rows produced by a statement.

    Driver backed results are single pass: every row is returned once, by whichever fetch method reaches it first.
    Values are returned as the driver produced them (no bool/datetime parsing; see :meth:`DialectABC.parse_bool`
    and :meth:`DialectABC.parse_datetime`). Statements without a result set yield no columns and no rows.
    """

    @abstractmethod
    def columns(self) -> dict[str, str | None]:
        """Column name to native type name (``None`` when the driver cannot tell), in select order."""

    @abstractmethod
    def fetch_dict(self) -> dict[str, Any] | None:
        """The next row, or ``None`` when exhausted. (PHP ``fetchAssoc``.)"""

    @abstractmethod
    def fetch_dicts(self) -> list[dict[str, Any]]:
        """All remaining rows. (PHP ``fetchAssocs``.)"""

    def scalar(self, column: str | None = None) -> Any:
        """A single value of the next row: ``column``, or the first column. ``None`` when there is no row."""
        row = self.fetch_dict()

        if row is None:
            return None

        if column is not None:
            return row.get(column)

        return next(iter(row.values()), None)

    @overload
    def fetch_object(self) -> SimpleNamespace | None: ...

    @overload
    def fetch_object(self, cls: Callable[..., T], *args: Any, **kwargs: Any) -> T | None: ...

    def fetch_object(self, cls: Callable[..., Any] = SimpleNamespace, *args: Any, **kwargs: Any) -> Any:
        """The next row as an object: ``cls(*args, **kwargs)`` with every column set as an attribute."""
        row = self.fetch_dict()

        if row is None:
            return None

        return _to_object(row, cls, args, kwargs)

    @overload
    def fetch_objects(self) -> list[SimpleNamespace]: ...

    @overload
    def fetch_objects(self, cls: Callable[..., T], *args: Any, **kwargs: Any) -> list[T]: ...

    def fetch_objects(self, cls: Callable[..., Any] = SimpleNamespace, *args: Any, **kwargs: Any) -> list[Any]:
        """All remaining rows as objects; see :meth:`fetch_object`."""
        return [_to_object(row, cls, args, kwargs) for row in self.fetch_dicts()]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Iterate over the remaining rows."""
        while (row := self.fetch_dict()) is not None:
            yield row


def _to_object(row: dict[str, Any], cls: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    instance = cls(*args, **kwargs)

    for key, value in row.items():
        setattr(instance, key, value)

    return instance


__all__ = ["ResultABC"]
