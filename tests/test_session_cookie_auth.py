"""Regression tests for NanoKVM 2.5.1 session-cookie authentication."""

import asyncio
import io
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from aioresponses import aioresponses
from PIL import Image
import pytest
import yarl

from nanokvm.client import (
    NanoKVMAuthenticationFailure,
    NanoKVMClient,
    NanoKVMInvalidResponseError,
    NanoKVMNotAuthenticatedError,
)
from nanokvm.models import HWVersion

_BASE_URL = "http://kvm.local/api/"
_LOGIN_URL = f"{_BASE_URL}auth/login"
_HARDWARE_URL = f"{_BASE_URL}vm/hardware"
_INFO_URL = f"{_BASE_URL}vm/info"

_HARDWARE_PAYLOAD = {
    "code": 0,
    "msg": "success",
    "data": {"version": "PCIE"},
}


def _login_payload(data: object = None) -> dict[str, object]:
    return {"code": 0, "msg": "success", "data": data}


def _info_payload() -> dict[str, object]:
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "ips": [],
            "mdns": "kvm.local",
            "image": "v1.4.0",
            "application": "2.5.1",
            "deviceKey": "synthetic-device-key",
        },
    }


async def test_cookie_only_login_accepts_null_data_and_uses_fresh_cookie() -> None:
    """The 2.5.1 login envelope has no data and returns the token as a cookie."""
    async with NanoKVMClient(_BASE_URL) as client:
        with aioresponses() as mocked:
            mocked.post(
                _LOGIN_URL,
                payload=_login_payload(),
                headers={"Set-Cookie": "nano-kvm-token=cookie-token; Path=/; HttpOnly"},
            )
            mocked.get(_HARDWARE_URL, payload=_HARDWARE_PAYLOAD)

            await client.authenticate("synthetic-user", "synthetic-password")

            assert client.token == "cookie-token"
            login_calls = mocked.requests[("POST", yarl.URL(_LOGIN_URL))]
            assert login_calls[0].kwargs.get("cookies") == {}


async def test_cookie_token_has_priority_over_legacy_body_token() -> None:
    """A fresh Set-Cookie value wins when old firmware returns both tokens."""
    async with NanoKVMClient(_BASE_URL) as client:
        with aioresponses() as mocked:
            mocked.post(
                _LOGIN_URL,
                payload=_login_payload({"token": "body-token"}),
                headers={"Set-Cookie": "nano-kvm-token=cookie-token; Path=/"},
            )
            mocked.get(_HARDWARE_URL, payload=_HARDWARE_PAYLOAD)

            await client.authenticate("synthetic-user", "synthetic-password")

            assert client.token == "cookie-token"


async def test_legacy_body_token_login_remains_supported() -> None:
    """Older firmware continues to authenticate with a token in response data."""
    async with NanoKVMClient(_BASE_URL) as client:
        with aioresponses() as mocked:
            mocked.post(
                _LOGIN_URL,
                payload=_login_payload({"token": "body-token"}),
            )
            mocked.get(_HARDWARE_URL, payload=_HARDWARE_PAYLOAD)

            await client.authenticate("synthetic-user", "synthetic-password")

            assert client.token == "body-token"


async def test_cookie_login_validates_api_code_before_accepting_cookie() -> None:
    """A cookie on an API error response cannot establish a session."""
    async with NanoKVMClient(_BASE_URL, use_password_obfuscation=True) as client:
        with aioresponses() as mocked:
            mocked.post(
                _LOGIN_URL,
                payload={
                    "code": -2,
                    "msg": "invalid username or password",
                    "data": None,
                },
                headers={"Set-Cookie": "nano-kvm-token=invalid-token; Path=/"},
            )

            with pytest.raises(NanoKVMAuthenticationFailure):
                await client.authenticate("synthetic-user", "synthetic-password")

            assert client.token is None


async def test_cookie_login_works_with_dummy_cookie_jar() -> None:
    """Cookie extraction does not depend on the session cookie jar."""
    session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
    try:
        async with NanoKVMClient(_BASE_URL, session=session) as client:
            with aioresponses() as mocked:
                mocked.post(
                    _LOGIN_URL,
                    payload=_login_payload(),
                    headers={
                        "Set-Cookie": "nano-kvm-token=cookie-token; Path=/; HttpOnly"
                    },
                )
                mocked.get(_HARDWARE_URL, payload=_HARDWARE_PAYLOAD)

                await client.authenticate("synthetic-user", "synthetic-password")

                assert client.token == "cookie-token"
    finally:
        await session.close()


async def test_login_does_not_reuse_an_old_cookie_from_external_session() -> None:
    """A stale cookie jar value cannot make a tokenless login succeed."""
    cookie_jar = aiohttp.CookieJar()
    cookie_jar.update_cookies(
        {"nano-kvm-token": "old-cookie-token"},
        response_url=yarl.URL(_BASE_URL),
    )
    session = aiohttp.ClientSession(cookie_jar=cookie_jar)

    try:
        async with NanoKVMClient(_BASE_URL, session=session) as client:
            with aioresponses() as mocked:
                mocked.post(_LOGIN_URL, payload=_login_payload())

                with pytest.raises(NanoKVMInvalidResponseError, match="missing token"):
                    await client.authenticate("synthetic-user", "synthetic-password")

                assert client.token is None
    finally:
        await session.close()


async def test_empty_login_response_is_not_authentication_disabled() -> None:
    """An empty successful login is invalid even for the explicit disabled token."""
    async with NanoKVMClient(_BASE_URL, token="disabled") as client:
        with aioresponses() as mocked:
            mocked.post(_LOGIN_URL, payload=_login_payload())

            with pytest.raises(NanoKVMInvalidResponseError, match="missing token"):
                await client.authenticate("synthetic-user", "synthetic-password")

            assert client.token is None


@pytest.mark.parametrize("failure", ["credentials", "forbidden", "format", "network"])
async def test_failed_reauthentication_cannot_keep_previous_identity(
    failure: str,
) -> None:
    """A failed identity switch must not retain an old privileged session."""
    old_ws = AsyncMock()
    old_ws.closed = False
    async with NanoKVMClient(
        _BASE_URL, token="previous-admin", use_password_obfuscation=True
    ) as client:
        client._ws = old_ws
        client._mouse_buttons = 1
        with aioresponses() as mocked:
            if failure == "credentials":
                mocked.post(
                    _LOGIN_URL, payload={"code": -2, "msg": "invalid", "data": None}
                )
                error: type[Exception] = NanoKVMAuthenticationFailure
            elif failure == "forbidden":
                mocked.post(_LOGIN_URL, status=403, body='"forbidden"')
                error = aiohttp.ClientResponseError
            elif failure == "network":
                mocked.post(_LOGIN_URL, exception=aiohttp.ClientConnectionError())
                error = aiohttp.ClientConnectionError
            else:
                mocked.post(_LOGIN_URL, payload=_login_payload())
                error = NanoKVMInvalidResponseError
            with pytest.raises(error):
                await client.authenticate("other-user", "synthetic-password")
            assert client.token is None
            assert client._mouse_buttons == 0
            old_ws.close.assert_awaited_once()
            with pytest.raises(NanoKVMNotAuthenticatedError):
                await client.get_account()
            with pytest.raises(NanoKVMNotAuthenticatedError):
                await client._get_ws()
            assert len(mocked.requests[("POST", yarl.URL(_LOGIN_URL))]) == 1


async def test_reauthentication_clears_transport_even_when_token_is_reissued() -> None:
    """Transport state belongs to an authentication attempt, not a token string."""
    old_ws = AsyncMock()
    old_ws.closed = False
    async with NanoKVMClient(_BASE_URL, token="same-token") as client:
        client._ws = old_ws
        client._mouse_buttons = 1
        with aioresponses() as mocked:
            mocked.post(_LOGIN_URL, payload=_login_payload({"token": "same-token"}))
            mocked.get(_HARDWARE_URL, payload=_HARDWARE_PAYLOAD)
            await client.authenticate("synthetic-user", "synthetic-password")
        assert client.token == "same-token"
        assert client._ws is None
        assert client._mouse_buttons == 0
        old_ws.close.assert_awaited_once()


async def test_authentication_uses_received_token_for_http_requests() -> None:
    """The token obtained from a cookie is sent explicitly on later requests."""
    async with NanoKVMClient(_BASE_URL) as client:
        with aioresponses() as mocked:
            mocked.post(
                _LOGIN_URL,
                payload=_login_payload(),
                headers={"Set-Cookie": "nano-kvm-token=cookie-token; Path=/"},
            )
            mocked.get(_HARDWARE_URL, payload=_HARDWARE_PAYLOAD)
            mocked.get(_INFO_URL, payload=_info_payload())

            await client.authenticate("synthetic-user", "synthetic-password")
            await client.get_info()

            info_calls = mocked.requests[("GET", yarl.URL(_INFO_URL))]
            assert info_calls[0].kwargs.get("cookies") == {
                "nano-kvm-token": "cookie-token"
            }


async def test_authentication_closes_previous_websocket_before_replacing_identity() -> (
    None
):
    """Re-authentication resets transport state before storing the new token."""
    old_ws = AsyncMock()
    old_ws.closed = False
    new_ws = AsyncMock()
    new_ws.closed = False

    with patch(
        "aiohttp.ClientSession.ws_connect",
        new_callable=AsyncMock,
        side_effect=[old_ws, new_ws],
    ):
        async with NanoKVMClient(_BASE_URL, token="old-token") as client:
            client._hw_version = HWVersion.PCIE
            client._application_version = "2.5.1"
            await client.mouse_move_rel(0.1, 0.0)

            with aioresponses() as mocked:
                mocked.post(
                    _LOGIN_URL,
                    payload=_login_payload(),
                    headers={"Set-Cookie": "nano-kvm-token=new-token; Path=/"},
                )
                mocked.get(_HARDWARE_URL, payload=_HARDWARE_PAYLOAD)

                await client.authenticate("other-user", "synthetic-password")

            assert old_ws.close.await_count == 1
            assert client.token == "new-token"
            assert client._mouse_buttons == 0


async def test_mjpeg_request_uses_cookie_login_token() -> None:
    """The MJPEG path uses the same explicit session token as JSON requests."""
    image_buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(1, 2, 3)).save(image_buffer, format="JPEG")
    image_data = image_buffer.getvalue()

    async def stream_handler(request: web.Request) -> web.StreamResponse:
        assert request.headers.get("Cookie") == "nano-kvm-token=cookie-token"
        response = web.StreamResponse(
            headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"}
        )
        await response.prepare(request)
        await response.write(
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(image_data)}\r\n\r\n".encode()
            + image_data
            + b"\r\n--frame--\r\n"
        )
        await response.write_eof()
        return response

    application = web.Application()
    application.router.add_get("/api/stream/mjpeg", stream_handler)
    runner = aiohttp.web.AppRunner(application)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = getattr(site._server, "sockets", None)
    assert sockets
    port = sockets[0].getsockname()[1]

    try:
        async with NanoKVMClient(f"http://127.0.0.1:{port}/api/") as client:
            client._token = "cookie-token"
            async with asyncio.timeout(2):
                frames = [frame async for frame in client.mjpeg_stream()]
            assert len(frames) == 1
    finally:
        await runner.cleanup()
