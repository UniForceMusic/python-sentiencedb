"""Per-thread connections with an optional cap on how many are open at once."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Generic, TypeVar

from ..exceptions import AdapterError, ConnectionLimitError

T = TypeVar("T")


class _ExitHook:
    """Calls ``callback`` when the thread that owns it exits.

    It lives in a :class:`threading.local`, which CPython clears while tearing the thread down; ``__del__`` then
    runs in the dying thread.
    """

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    def disarm(self) -> None:
        """Do nothing when the thread exits (its value was released early)."""
        self._callback = _noop

    def __del__(self) -> None:
        # Thread teardown: nobody could handle an exception raised here.
        with contextlib.suppress(Exception):
            self._callback()


def _noop() -> None:
    pass


class _ThreadSlot(threading.local):
    def __init__(self) -> None:
        self.exit_hook: _ExitHook | None = None


class ThreadLocalStore(Generic[T]):
    """Opens one value (connection) per thread on first use and closes it when the thread exits, or earlier through
    :meth:`release_current`.

    :param create: Creates a value for the calling thread.
    :param dispose: Closes a value; called from whichever thread evicts it.
    :param shared: Use a single value for every thread instead (SQLite in-memory databases).
    :param max_values: Cap on values open at once; a thread that needs a new one waits for a free slot.
    :param acquire_timeout: Seconds to wait for a free slot before :class:`ConnectionLimitError`; ``None`` waits
        forever.
    """

    def __init__(
        self,
        create: Callable[[], T],
        dispose: Callable[[T], None],
        *,
        shared: bool = False,
        max_values: int | None = None,
        acquire_timeout: float | None = None,
    ) -> None:
        if max_values is not None and max_values < 1:
            raise AdapterError("max_connections must be at least 1")

        self._create = create
        self._dispose = dispose
        self._shared = shared
        self._max_values = max_values
        self._acquire_timeout = acquire_timeout
        self._slots = threading.BoundedSemaphore(max_values) if max_values is not None else None
        self._lock = threading.Lock()
        self._values: dict[threading.Thread | None, T] = {}
        self._thread_slot = _ThreadSlot()
        self._closed = False

    def get(self) -> T:
        """The calling thread's value, opened on first use.

        :raises AdapterError: The store is closed.
        :raises ConnectionLimitError: No slot came free within ``acquire_timeout``.
        """
        key = None if self._shared else threading.current_thread()

        with self._lock:
            self._ensure_open()
            value = self._values.get(key)

        if value is not None:
            return value

        self._close_finished_threads()
        self._acquire_slot()

        try:
            value = self._create()
        except BaseException:
            self._release_slot()
            raise

        with self._lock:
            closed = self._closed

            if not closed:
                self._values[key] = value

        if closed:
            self._discard(value)
            self._ensure_open()

        if key is not None:
            self._thread_slot.exit_hook = _ExitHook(lambda: self._evict(key))

        return value

    def current(self) -> T | None:
        """The calling thread's value when it has one open, without opening it."""
        key = None if self._shared else threading.current_thread()

        with self._lock:
            return self._values.get(key)

    def release_current(self) -> bool:
        """Close the calling thread's value and free its slot; the next :meth:`get` opens a new one.

        A shared store keeps its single value: closing it would lose state every thread depends on (an in-memory
        database).

        :returns: Whether a value was closed.
        """
        if self._shared:
            return False

        key = threading.current_thread()

        with self._lock:
            value = self._values.pop(key, None)

        hook = self._thread_slot.exit_hook
        self._thread_slot.exit_hook = None

        if hook is not None:
            hook.disarm()

        if value is None:
            return False

        self._discard(value)

        return True

    def close(self) -> None:
        """Close every value; the store cannot be used afterwards."""
        with self._lock:
            self._closed = True
            values = list(self._values.values())
            self._values.clear()

        for value in values:
            self._discard(value)

    def __len__(self) -> int:
        """Number of open values."""
        with self._lock:
            return len(self._values)

    def _ensure_open(self) -> None:
        if self._closed:
            raise AdapterError("the adapter is closed")

    def _acquire_slot(self) -> None:
        if self._slots is None:
            return

        if not self._slots.acquire(timeout=self._acquire_timeout):
            raise ConnectionLimitError(
                f"no connection came free within {self._acquire_timeout}s (max_connections is {self._max_values})"
            )

    def _release_slot(self) -> None:
        if self._slots is not None:
            self._slots.release()

    def _evict(self, key: threading.Thread) -> None:
        with self._lock:
            value = self._values.pop(key, None)

        if value is not None:
            self._discard(value)

    def _close_finished_threads(self) -> None:
        """Backstop for the exit hook (e.g. a value opened by a thread that never ran its teardown)."""
        with self._lock:
            finished = [key for key in self._values if key is not None and not key.is_alive()]
            values = [self._values.pop(key) for key in finished]

        for value in values:
            self._discard(value)

    def _discard(self, value: T) -> None:
        try:
            self._dispose(value)
        finally:
            self._release_slot()


__all__ = ["ThreadLocalStore"]
