"""Blocking client for NanoKVM devices."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Coroutine, Iterator
import concurrent.futures
import functools
import inspect
import threading
from types import TracebackType
from typing import Any, TypeVar

from .client import NanoKVMClient

__all__ = ["NanoKVMSyncClient"]

T = TypeVar("T")

# Their results stay bound to the client's event loop; use ``run()`` instead.
_ASYNC_ONLY = frozenset({"open_ws"})

# Wait in short slices: before Python 3.14, an untimed wait on Windows ignores
# Ctrl-C until the operation finishes.
_WAIT_INTERVAL = 0.1


async def _anext(agen: AsyncGenerator[T, None]) -> T:
    return await anext(agen)


async def _aclose(agen: AsyncGenerator[Any, None]) -> None:
    await agen.aclose()


class NanoKVMSyncClient:
    """Blocking client for the NanoKVM, for code that does not use asyncio.

    Every public coroutine method of ``NanoKVMClient`` is available as a
    blocking method with the same arguments, results and exceptions. Methods
    that stream return iterators. The async client runs on a private event
    loop in a background thread.
    """

    def __init__(self, url: str, **kwargs: Any) -> None:
        """Start the client. Arguments are those of ``NanoKVMClient``.

        ``session`` is not supported: the client owns its HTTP session.
        """
        if "session" in kwargs:
            raise TypeError("NanoKVMSyncClient does not accept a session")

        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="nanokvm-sync-client", daemon=True
        )
        self._thread.start()
        try:
            self._client = self.run(self._start(url, kwargs))
        except BaseException:
            self._stop(None)
            raise

    @property
    def client(self) -> NanoKVMClient:
        """The wrapped async client. Await its coroutines through ``run()``."""
        return self._client

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run a coroutine on the client's event loop and return its result.

        Use it for typed results, such as ``run(client.get_gpio())``, or for
        the async-only API. An interrupt, such as Ctrl-C, cancels it.
        """
        if self._closed:
            coro.close()
            raise RuntimeError("NanoKVMSyncClient is closed")
        if threading.current_thread() is self._thread:
            coro.close()
            raise RuntimeError(
                "NanoKVMSyncClient cannot be called from its own event loop, "
                "such as from a progress callback"
            )

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            while not future.done():
                concurrent.futures.wait((future,), timeout=_WAIT_INTERVAL)
        except BaseException:
            future.cancel()
            raise
        return future.result()

    def close(self) -> None:
        """Close the client and stop its event loop. Repeated calls do nothing."""
        if not self._closed:
            self._stop(self._client)

    def __enter__(self) -> NanoKVMSyncClient:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Context manager exit - close the client."""
        self.close()

    def __getattr__(self, name: str) -> Any:
        """Expose the async client's public API as blocking calls."""
        if name.startswith("_"):
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            )
        if name in _ASYNC_ONLY:
            raise AttributeError(
                f"{name} is only available on NanoKVMClient; "
                f"call it through run(client.{name}(...))"
            )
        try:
            attr = getattr(self._client, name)
        except AttributeError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            ) from None

        if inspect.isasyncgenfunction(attr):

            @functools.wraps(attr)
            def iterate(*args: Any, **kwargs: Any) -> Iterator[Any]:
                return self._iterate(attr(*args, **kwargs))

            return iterate

        if inspect.iscoroutinefunction(attr):

            @functools.wraps(attr)
            def call(*args: Any, **kwargs: Any) -> Any:
                return self.run(attr(*args, **kwargs))

            return call

        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        """Set the async client's properties, such as ``url``, on the client."""
        if not name.startswith("_") and isinstance(
            getattr(NanoKVMClient, name, None), property
        ):
            setattr(self._client, name, value)
        else:
            super().__setattr__(name, value)

    def __dir__(self) -> list[str]:
        """List the blocking API next to the client's own attributes."""
        public = {name for name in dir(self._client) if not name.startswith("_")}
        return sorted(set(super().__dir__()) | (public - _ASYNC_ONLY))

    def _iterate(self, agen: AsyncGenerator[T, None]) -> Iterator[T]:
        """Drive an async generator from the calling thread."""
        while True:
            try:
                item = self.run(_anext(agen))
            except StopAsyncIteration:
                return
            try:
                yield item
            except BaseException:
                # The caller stopped early, while the async generator waits at a
                # yield. A step that raised or was cancelled already ended it.
                if not self._closed:
                    self.run(_aclose(agen))
                raise

    @staticmethod
    async def _start(url: str, kwargs: dict[str, Any]) -> NanoKVMClient:
        client = NanoKVMClient(url, **kwargs)
        await client.__aenter__()
        return client

    async def _shutdown(self, client: NanoKVMClient | None) -> None:
        # Close open streams before the HTTP session they read from.
        await self._loop.shutdown_asyncgens()
        if client is not None:
            await client.__aexit__(None, None, None)
        await self._loop.shutdown_default_executor()

    def _stop(self, client: NanoKVMClient | None) -> None:
        try:
            self.run(self._shutdown(client))
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()
