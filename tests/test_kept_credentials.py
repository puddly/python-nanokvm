"""Tests for credentials kept in memory to log in again."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import (
    NanoKVMAuthenticationFailure,
    NanoKVMClient,
    NanoKVMNotAuthenticatedError,
)
from nanokvm.components.session import _Credentials
from nanokvm.models import ApiResponseCode, GpioType, HWVersion

from .common import BASE_URL, mark_detected

_LOGIN_URL = f"{BASE_URL}auth/login"
_HARDWARE_URL = f"{BASE_URL}vm/hardware"
_GPIO_URL = f"{BASE_URL}vm/gpio"
_USERNAME = "synthetic-user"
_PASSWORD = "synthetic-password"


def _ok(data: object = None) -> dict[str, object]:
    return {"code": 0, "msg": "success", "data": data}


_REJECTED = {
    "code": ApiResponseCode.INVALID_USERNAME_OR_PASSWORD.value,
    "msg": "invalid username or password",
    "data": None,
}


def _mock_login(mocked: aioresponses, token: str) -> None:
    mocked.post(_LOGIN_URL, payload=_ok({"token": token}))


def _login_bodies(mocked: aioresponses) -> list[object]:
    return [
        call.kwargs["json"]
        for call in mocked.requests.get(("POST", yarl.URL(_LOGIN_URL)), [])
    ]


@pytest.fixture
async def kept_client(mocked: aioresponses) -> AsyncIterator[NanoKVMClient]:
    """A client that kept the credentials of its login."""
    async with NanoKVMClient(
        BASE_URL, keep_credentials=True, use_password_obfuscation=False
    ) as client:
        _mock_login(mocked, "first-token")
        mocked.get(_HARDWARE_URL, payload=_ok({"version": "PCIE"}))
        await client.authenticate(_USERNAME, _PASSWORD)
        yield client


async def test_credentials_are_not_kept_by_default(mocked: aioresponses) -> None:
    """Without the opt-in, the client never keeps the password."""
    async with NanoKVMClient(BASE_URL, use_password_obfuscation=False) as client:
        _mock_login(mocked, "token")
        mocked.get(_HARDWARE_URL, payload=_ok({"version": "PCIE"}))
        await client.authenticate(_USERNAME, _PASSWORD)

        assert client._session._credentials is None
        with pytest.raises(NanoKVMNotAuthenticatedError, match="No credentials"):
            await client._session.reauthenticate()


async def test_reauthenticate_logs_in_with_kept_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A kept login is replayed and the new token replaces the old one."""
    _mock_login(mocked, "second-token")

    await kept_client._session.reauthenticate()

    assert kept_client.token == "second-token"
    assert _login_bodies(mocked) == [
        {"username": _USERNAME, "password": _PASSWORD},
        {"username": _USERNAME, "password": _PASSWORD},
    ]


def test_kept_password_is_hidden_from_repr() -> None:
    """Debug output never shows the kept password."""
    text = repr(_Credentials(_USERNAME, _PASSWORD))

    assert _USERNAME in text
    assert _PASSWORD not in text


async def test_unreachable_device_keeps_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A login that cannot reach the device can be retried later."""
    mocked.post(_LOGIN_URL, exception=aiohttp.ClientConnectionError())
    with pytest.raises(aiohttp.ClientConnectionError):
        await kept_client._session.reauthenticate()

    _mock_login(mocked, "second-token")
    await kept_client._session.reauthenticate()
    assert kept_client.token == "second-token"


async def test_rejected_credentials_are_dropped(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Credentials the device rejects are not tried again."""
    mocked.post(_LOGIN_URL, payload=_REJECTED)

    with pytest.raises(NanoKVMAuthenticationFailure):
        await kept_client._session.reauthenticate()

    assert kept_client._session._credentials is None


async def test_failed_login_drops_the_previous_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A failed switch to another account keeps neither account."""
    mocked.post(_LOGIN_URL, payload=_REJECTED)

    with pytest.raises(NanoKVMAuthenticationFailure):
        await kept_client.authenticate("other-user", "wrong-password")

    assert kept_client._session._credentials is None


async def test_logout_drops_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Logging out forgets the kept credentials."""
    mocked.post(f"{BASE_URL}auth/logout", payload=_ok())

    await kept_client.logout()

    assert kept_client._session._credentials is None


async def test_password_change_drops_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The old password is not kept after it changes."""
    mark_detected(kept_client, HWVersion.PCIE, "2.4.3")
    mocked.post(f"{BASE_URL}auth/password", payload=_ok())

    await kept_client.change_password(_USERNAME, "new-synthetic-password")

    assert kept_client._session._credentials is None


async def test_closing_the_client_drops_credentials(mocked: aioresponses) -> None:
    """Credentials do not outlive the client."""
    async with NanoKVMClient(
        BASE_URL, keep_credentials=True, use_password_obfuscation=False
    ) as client:
        _mock_login(mocked, "token")
        mocked.get(_HARDWARE_URL, payload=_ok({"version": "PCIE"}))
        await client.authenticate(_USERNAME, _PASSWORD)
        assert client._session._credentials is not None

    assert client._session._credentials is None


def _tokens(mocked: aioresponses, method: str, url: str) -> list[str]:
    return [
        call.kwargs["cookies"]["nano-kvm-token"]
        for call in mocked.requests.get((method, yarl.URL(url)), [])
    ]


async def test_a_rejected_request_logs_in_again_and_is_sent_once_more(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A lost session is renewed and the request repeated with the new token."""
    mark_detected(kept_client, HWVersion.PCIE, "2.4.3")
    generation = kept_client._session._session_generation
    mocked.post(_GPIO_URL, status=401)
    _mock_login(mocked, "second-token")
    mocked.post(_GPIO_URL, payload=_ok())

    await kept_client.push_button(GpioType.POWER, 800)

    assert kept_client.token == "second-token"
    assert _tokens(mocked, "POST", _GPIO_URL) == ["first-token", "second-token"]
    calls = mocked.requests[("POST", yarl.URL(_GPIO_URL))]
    assert [call.kwargs["json"] for call in calls] == [
        {"type": "power", "duration": 800}
    ] * 2
    assert len(_login_bodies(mocked)) == 2
    # Same account: operations in progress are not invalidated.
    assert kept_client._session._session_generation == generation
    # The server may have restarted on a new version: detect it again.
    assert kept_client.application_version is None


async def test_a_second_rejection_is_not_retried(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A request rejected again after the login ends the session."""
    mocked.post(_GPIO_URL, status=401)
    _mock_login(mocked, "second-token")
    mocked.post(_GPIO_URL, status=401)

    with pytest.raises(NanoKVMNotAuthenticatedError):
        await kept_client.push_button(GpioType.POWER, 800)

    assert len(_login_bodies(mocked)) == 2
    assert kept_client.token is None


async def test_without_kept_credentials_a_rejection_ends_the_session(
    mocked: aioresponses,
) -> None:
    """Clients that do not keep credentials behave as before."""
    async with NanoKVMClient(BASE_URL, token="first-token") as client:
        mocked.post(_GPIO_URL, status=401)

        with pytest.raises(NanoKVMNotAuthenticatedError):
            await client.push_button(GpioType.POWER, 800)

        assert client.token is None
    assert not _login_bodies(mocked)


async def test_rejected_credentials_end_the_session_when_renewing(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Credentials the device no longer accepts are dropped, not retried."""
    mocked.post(_GPIO_URL, status=401)
    mocked.post(_LOGIN_URL, payload=_REJECTED)

    with pytest.raises(NanoKVMAuthenticationFailure):
        await kept_client.push_button(GpioType.POWER, 800)

    assert kept_client._session._credentials is None
    assert kept_client.token is None


async def test_unreachable_device_while_renewing_keeps_the_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The next call renews the session once the device answers again."""
    mocked.post(_GPIO_URL, status=401)
    mocked.post(_LOGIN_URL, exception=aiohttp.ClientConnectionError())

    with pytest.raises(aiohttp.ClientConnectionError):
        await kept_client.push_button(GpioType.POWER, 800)
    assert kept_client._session._credentials is not None

    mocked.post(_GPIO_URL, status=401)
    _mock_login(mocked, "second-token")
    mocked.post(_GPIO_URL, payload=_ok())
    await kept_client.push_button(GpioType.POWER, 800)

    assert kept_client.token == "second-token"


async def test_uploads_are_not_sent_twice(
    kept_client: NanoKVMClient, mocked: aioresponses, tmp_path: Path
) -> None:
    """A multipart body cannot be repeated, so an upload ends the session."""
    mark_detected(kept_client, HWVersion.PCIE, "2.5.2")
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic package")
    mocked.post(f"{BASE_URL}application/update/offline", status=401)

    with pytest.raises(NanoKVMNotAuthenticatedError):
        await kept_client.update_application_offline(package)

    assert len(_login_bodies(mocked)) == 1


async def test_a_stream_logs_in_again(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Starting the MJPEG stream on a lost session renews it."""
    stream_url = f"{BASE_URL}stream/mjpeg"
    mocked.get(stream_url, status=401)
    _mock_login(mocked, "second-token")
    mocked.get(
        stream_url,
        headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"},
        body=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8one\r\n--frame--\r\n",
    )

    frames = [frame async for frame in kept_client.mjpeg_stream_bytes()]

    assert frames == [b"\xff\xd8one"]
    assert _tokens(mocked, "GET", stream_url) == ["first-token", "second-token"]


@asynccontextmanager
async def _device(
    login_delay: float = 0,
) -> AsyncGenerator[tuple[NanoKVMClient, dict[str, Any]], None]:
    """A logged-in client and a device accepting only ``state["valid"]`` tokens."""
    state: dict[str, Any] = {"valid": set(), "logins": []}

    def rejected(request: web.Request) -> web.Response | None:
        if request.cookies.get("nano-kvm-token") in state["valid"]:
            return None
        return web.json_response("unauthorized", status=401)

    async def login(request: web.Request) -> web.Response:
        username = (await request.json())["username"]
        state["logins"].append(username)
        await asyncio.sleep(login_delay)
        token = f"{username}-{len(state['logins'])}"
        state["valid"].add(token)
        return web.json_response(_ok({"token": token}))

    async def hardware(request: web.Request) -> web.Response:
        return web.json_response(_ok({"version": "PCIE"}))

    async def gpio(request: web.Request) -> web.Response:
        return rejected(request) or web.json_response(_ok({"pwr": True, "hdd": False}))

    async def socket(request: web.Request) -> web.StreamResponse:
        if (response := rejected(request)) is not None:
            return response
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            pass
        return ws

    application = web.Application()
    application.router.add_post("/api/auth/login", login)
    application.router.add_get("/api/vm/hardware", hardware)
    application.router.add_get("/api/vm/gpio", gpio)
    application.router.add_get("/api/ws", socket)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        async with NanoKVMClient(
            f"http://127.0.0.1:{runner.addresses[0][1]}/api/",
            keep_credentials=True,
            use_password_obfuscation=False,
        ) as client:
            await client.authenticate(_USERNAME, _PASSWORD)
            yield client, state
    finally:
        await runner.cleanup()


async def test_concurrent_rejections_share_one_login() -> None:
    """Calls that lose the same session wait for a single new login."""
    async with _device(login_delay=0.05) as (client, state):
        state["valid"].clear()

        results = await asyncio.gather(*(client.get_gpio() for _ in range(3)))

    assert all(result.pwr for result in results)
    assert state["logins"] == [_USERNAME, _USERNAME]


async def test_a_new_login_wins_over_a_renewal_in_progress() -> None:
    """Renewing never replaces a session the caller started meanwhile."""
    async with _device(login_delay=0.1) as (client, state):
        state["valid"].clear()

        renewing = asyncio.create_task(client.get_gpio())
        await asyncio.sleep(0.02)
        await client.authenticate("other-user", "other-password")

        with pytest.raises(NanoKVMNotAuthenticatedError, match="Session changed"):
            await renewing
        assert client.token == "other-user-3"


@pytest.mark.parametrize("shared", [False, True])
async def test_a_websocket_handshake_logs_in_again(shared: bool) -> None:
    """The keyboard/mouse socket and open_ws() renew a lost session."""
    async with _device() as (client, state):
        state["valid"].clear()

        if shared:
            await client._session.get_ws()
        else:
            ws = await client.open_ws("ws")
            await ws.close()

        assert client.token == f"{_USERNAME}-2"
    assert state["logins"] == [_USERNAME, _USERNAME]
