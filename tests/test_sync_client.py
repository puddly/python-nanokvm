"""Tests for the blocking client."""

import asyncio
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
import threading
from typing import Any
from unittest.mock import AsyncMock, patch

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import GetGpioRsp, GpioType, HWVersion
from nanokvm.sync_client import NanoKVMSyncClient

from .common import BASE_URL, mark_detected

_GPIO_URL = f"{BASE_URL}vm/gpio"
_THREAD_NAME = "nanokvm-sync-client"


def _ok(data: object = None) -> dict[str, object]:
    return {"code": 0, "msg": "success", "data": data}


def _client_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == _THREAD_NAME]


@pytest.fixture
def kvm() -> Iterator[NanoKVMSyncClient]:
    """A blocking client that already holds a session token."""
    with NanoKVMSyncClient(BASE_URL, token="test-token") as kvm:
        yield kvm


def test_blocking_calls_authenticate_and_return_results(
    mocked: aioresponses,
) -> None:
    """Coroutine methods block and return what the async client returns."""
    mocked.post(f"{BASE_URL}auth/login", payload=_ok({"token": "new-token"}))
    mocked.get(f"{BASE_URL}vm/hardware", payload=_ok({"version": "PCIE"}))
    mocked.get(_GPIO_URL, payload=_ok({"pwr": True, "hdd": False}))
    mocked.post(_GPIO_URL, payload=_ok())

    with NanoKVMSyncClient(BASE_URL, use_password_obfuscation=False) as kvm:
        kvm.authenticate("synthetic-user", "synthetic-password")
        assert kvm.token == "new-token"
        assert kvm.hw_version is HWVersion.PCIE

        assert kvm.get_gpio() == GetGpioRsp(pwr=True, hdd=False)
        kvm.push_button(GpioType.POWER, duration_ms=6000)

    call = mocked.requests[("POST", yarl.URL(_GPIO_URL))][0]
    assert call.kwargs["json"] == {"type": "power", "duration": 6000}


def test_run_returns_typed_results(
    kvm: NanoKVMSyncClient, mocked: aioresponses
) -> None:
    """run() awaits a coroutine of the wrapped client on its event loop."""
    mocked.get(_GPIO_URL, payload=_ok({"pwr": False, "hdd": False}))

    assert kvm.run(kvm.client.get_gpio()) == GetGpioRsp(pwr=False, hdd=False)


def test_run_reaches_the_async_only_api(kvm: NanoKVMSyncClient) -> None:
    """open_ws() works through run(), on the client's own event loop."""
    mock_ws = AsyncMock()
    mock_ws.receive.return_value = "offer"

    async def read_message(client: NanoKVMClient) -> Any:
        ws = await client.open_ws("stream/h264/webrtc")
        try:
            return await ws.receive()
        finally:
            await ws.close()

    with patch(
        "aiohttp.ClientSession.ws_connect", new_callable=AsyncMock, return_value=mock_ws
    ) as ws_connect:
        assert kvm.run(read_message(kvm.client)) == "offer"

    assert str(ws_connect.call_args.args[0]).endswith("/api/stream/h264/webrtc")
    mock_ws.close.assert_awaited_once()


def test_errors_propagate_unchanged(
    kvm: NanoKVMSyncClient, mocked: aioresponses
) -> None:
    """Client and transport exceptions reach the caller as they are."""
    mark_detected(kvm.client, HWVersion.PCIE)
    with pytest.raises(NanoKVMNotSupportedError, match="get_led_strip"):
        kvm.get_led_strip()

    # A timeout raised by the call is not mistaken for the wait between polls.
    mocked.get(_GPIO_URL, exception=TimeoutError())
    with pytest.raises(TimeoutError):
        kvm.get_gpio()


def test_streams_become_iterators(
    kvm: NanoKVMSyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Async generators become iterators that close their stream when left."""
    closed = threading.Event()

    async def frames(self: NanoKVMClient) -> AsyncGenerator[bytes, None]:
        closed.clear()
        try:
            for frame in (b"one", b"two", b"three"):
                yield frame
        finally:
            closed.set()

    monkeypatch.setattr(NanoKVMClient, "mjpeg_stream_bytes", frames)

    assert list(kvm.mjpeg_stream_bytes()) == [b"one", b"two", b"three"]
    assert closed.is_set()

    for _ in kvm.mjpeg_stream_bytes():
        break
    assert closed.is_set()

    frames_iter = kvm.mjpeg_stream_bytes()
    assert next(frames_iter) == b"one"
    frames_iter.close()
    assert closed.is_set()


def test_interrupt_cancels_the_call_in_progress(
    kvm: NanoKVMSyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C while waiting cancels the operation on the event loop."""
    started = threading.Event()
    cancelled = threading.Event()

    async def get_gpio(self: NanoKVMClient) -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def interrupted_wait(*args: Any, **kwargs: Any) -> None:
        assert started.wait(5)
        raise KeyboardInterrupt

    monkeypatch.setattr(NanoKVMClient, "get_gpio", get_gpio)
    with (
        patch("concurrent.futures.wait", side_effect=interrupted_wait),
        pytest.raises(KeyboardInterrupt),
    ):
        kvm.get_gpio()

    assert cancelled.wait(5)


def test_only_the_public_blocking_api_is_exposed(kvm: NanoKVMSyncClient) -> None:
    """Private and async-only attributes are not proxied."""
    names = dir(kvm)
    assert {"authenticate", "push_button", "mjpeg_stream", "run"} <= set(names)
    assert "open_ws" not in names

    with pytest.raises(AttributeError, match="_session"):
        kvm._session  # noqa: B018
    with pytest.raises(AttributeError, match=r"run\(client\.open_ws"):
        kvm.open_ws  # noqa: B018
    with pytest.raises(AttributeError, match="'NanoKVMSyncClient'.*'no_such'"):
        kvm.no_such  # noqa: B018


def test_setting_properties_reaches_the_client(
    kvm: NanoKVMSyncClient, mocked: aioresponses
) -> None:
    """Assigned properties change the wrapped client, not the wrapper."""
    new_url = "http://new-kvm.local/api/"
    mocked.get(f"{new_url}vm/gpio", payload=_ok({"pwr": True, "hdd": False}))

    kvm.url = yarl.URL(new_url)

    assert kvm.client.url == yarl.URL(new_url)
    assert kvm.get_gpio() == GetGpioRsp(pwr=True, hdd=False)

    with pytest.raises(AttributeError, match="token"):
        kvm.token = "other-token"
    assert kvm.token == "test-token"


def test_calls_from_the_event_loop_thread_are_rejected(
    kvm: NanoKVMSyncClient,
) -> None:
    """A callback running on the client's loop cannot block on the client."""

    async def reenter() -> None:
        kvm.get_gpio()

    with pytest.raises(RuntimeError, match="own event loop"):
        kvm.run(reenter())


def test_close_releases_the_client_and_its_thread() -> None:
    """close() closes the HTTP session, stops the thread and can be repeated."""
    kvm = NanoKVMSyncClient(BASE_URL)
    thread = kvm._thread

    kvm.close()
    kvm.close()

    assert not thread.is_alive()
    assert kvm.client._session._http_session is None
    with pytest.raises(RuntimeError, match="closed"):
        kvm.get_gpio()


def test_failed_start_stops_the_thread(tmp_path: Path) -> None:
    """A client that cannot start leaves no event loop thread behind."""
    with pytest.raises(FileNotFoundError):
        NanoKVMSyncClient(BASE_URL, ssl_ca_cert=str(tmp_path / "missing.pem"))

    assert not _client_threads()


def test_session_is_rejected() -> None:
    """An external aiohttp session would belong to another event loop."""
    with pytest.raises(TypeError, match="session"):
        NanoKVMSyncClient(BASE_URL, session=object())

    assert not _client_threads()


async def test_usable_while_an_event_loop_is_running(mocked: aioresponses) -> None:
    """The private loop lets blocking code run where a loop already runs."""
    mocked.get(_GPIO_URL, payload=_ok({"pwr": True, "hdd": True}))

    with NanoKVMSyncClient(BASE_URL, token="test-token") as kvm:
        assert kvm.get_gpio().pwr is True
