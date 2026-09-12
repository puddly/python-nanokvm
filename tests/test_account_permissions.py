"""Regression tests for NanoKVM account roles and permission failures."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aioresponses import aioresponses
from multidict import CIMultiDict, CIMultiDictProxy
import pytest
import yarl

from nanokvm.client import (
    NanoKVMApiError,
    NanoKVMClient,
    NanoKVMNotAuthenticatedError,
    NanoKVMPermissionError,
)
from nanokvm.models import GetAccountRsp, HWVersion

_BASE_URL = "http://kvm.local/api/"


def _websocket_request_info() -> aiohttp.RequestInfo:
    """Build a typed request descriptor for a synthetic handshake failure."""
    return aiohttp.RequestInfo(
        yarl.URL("ws://kvm.local/api/ws"),
        "GET",
        CIMultiDictProxy(CIMultiDict()),
    )


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


async def test_get_account_exposes_known_role() -> None:
    """The role returned by 2.5.1 is available to callers."""
    async with NanoKVMClient(_BASE_URL, token="synthetic-token") as client:
        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}auth/account",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {"username": "synthetic-user", "role": "user"},
                },
            )

            account = await client.get_account()

            assert account.username == "synthetic-user"
            assert account.role == "user"


@pytest.mark.parametrize("role", [None, "admin", "user", "future-role"])
def test_account_role_is_optional_and_forward_compatible(role: str | None) -> None:
    """Missing and future role values do not break model deserialization."""
    data: dict[str, str] = {"username": "synthetic-user"}
    if role is not None:
        data["role"] = role

    account = GetAccountRsp.model_validate(data)

    assert account.username == "synthetic-user"
    assert account.role == role


@pytest.mark.parametrize(
    "body",
    [b'"forbidden"', b"", b"<html><body>forbidden</body></html>"],
)
async def test_http_403_is_permission_error_before_body_parsing(body: bytes) -> None:
    """All real 403 bodies map to one structured permission exception."""
    async with NanoKVMClient(_BASE_URL, token="synthetic-token") as client:
        with aioresponses() as mocked:
            mocked.get(f"{_BASE_URL}storage/image", status=403, body=body)

            with pytest.raises(NanoKVMPermissionError) as exc_info:
                await client.get_images()

            error = exc_info.value
            assert error.status == 403
            assert error.method == "GET"
            assert error.path == "/storage/image"


async def test_permission_error_does_not_invalidate_session_or_retry_login() -> None:
    """A forbidden admin resource leaves the same user session usable."""
    async with NanoKVMClient(_BASE_URL, token="synthetic-token") as client:
        with aioresponses() as mocked:
            mocked.get(f"{_BASE_URL}storage/image", status=403, body=b'"forbidden"')
            mocked.get(f"{_BASE_URL}vm/info", payload=_info_payload())

            with pytest.raises(NanoKVMPermissionError):
                await client.get_images()

            info = await client.get_info()

            assert info.application == "2.5.1"
            assert client.token == "synthetic-token"
            assert (
                "POST",
                yarl.URL(f"{_BASE_URL}auth/login"),
            ) not in mocked.requests


async def test_http_401_invalidates_local_session() -> None:
    """An authenticated 401 clears the token and local input state."""
    async with NanoKVMClient(_BASE_URL, token="expired-token") as client:
        client._mouse_buttons = 1
        with aioresponses() as mocked:
            mocked.get(f"{_BASE_URL}vm/info", status=401, body=b'"unauthorized"')

            with pytest.raises(NanoKVMNotAuthenticatedError):
                await client.get_info()

            assert client.token is None
            assert client._mouse_buttons == 0

            with pytest.raises(NanoKVMNotAuthenticatedError):
                await client.get_info()


async def test_http_200_api_error_remains_api_error() -> None:
    """An API error envelope at HTTP 200 is distinct from permission failure."""
    async with NanoKVMClient(_BASE_URL, token="synthetic-token") as client:
        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}storage/image",
                payload={"code": -1, "msg": "read failed", "data": None},
            )

            with pytest.raises(NanoKVMApiError) as exc_info:
                await client.get_images()

            assert exc_info.value.code == -1
            assert client.token == "synthetic-token"


async def test_multipart_403_is_permission_error(tmp_path: Path) -> None:
    """Multipart endpoints use the same HTTP status classification."""
    script = tmp_path / "synthetic.sh"
    script.write_text("#!/bin/sh\ntrue\n")

    async with NanoKVMClient(_BASE_URL, token="synthetic-token") as client:
        with aioresponses() as mocked:
            mocked.post(f"{_BASE_URL}vm/script/upload", status=403, body=b"")

            with pytest.raises(NanoKVMPermissionError) as exc_info:
                await client.upload_script(script)

            assert exc_info.value.status == 403
            assert exc_info.value.method == "POST"
            assert exc_info.value.path == "/vm/script/upload"


async def test_websocket_403_is_permission_error() -> None:
    """A forbidden WebSocket handshake does not invalidate the token."""
    handshake_error = aiohttp.ClientResponseError(
        request_info=_websocket_request_info(),
        history=(),
        status=403,
        message="forbidden",
    )

    with patch(
        "aiohttp.ClientSession.ws_connect",
        new_callable=AsyncMock,
        side_effect=handshake_error,
    ):
        async with NanoKVMClient(_BASE_URL, token="synthetic-token") as client:
            client._hw_version = HWVersion.PCIE
            client._application_version = "2.5.1"

            with pytest.raises(NanoKVMPermissionError) as exc_info:
                await client.mouse_move_rel(0.1, 0.0)

            assert exc_info.value.status == 403
            assert exc_info.value.method == "GET"
            assert exc_info.value.path == "/ws"
            assert client.token == "synthetic-token"


async def test_websocket_401_clears_session_without_lock_deadlock() -> None:
    """Invalid WebSocket credentials clear state after releasing the lock."""
    handshake_error = aiohttp.ClientResponseError(
        request_info=_websocket_request_info(),
        history=(),
        status=401,
        message="unauthorized",
    )

    with patch(
        "aiohttp.ClientSession.ws_connect",
        new_callable=AsyncMock,
        side_effect=handshake_error,
    ):
        async with NanoKVMClient(_BASE_URL, token="expired-token") as client:
            client._hw_version = HWVersion.PCIE
            client._application_version = "2.5.1"
            client._mouse_buttons = 1

            with pytest.raises(NanoKVMNotAuthenticatedError):
                await asyncio.wait_for(client.mouse_move_rel(0.1, 0.0), timeout=1)

            assert client.token is None
            assert client._mouse_buttons == 0


async def test_mjpeg_403_is_permission_error() -> None:
    """Opening the MJPEG stream classifies forbidden status before parsing."""
    async with NanoKVMClient(_BASE_URL, token="synthetic-token") as client:
        with aioresponses() as mocked:
            mocked.get(f"{_BASE_URL}stream/mjpeg", status=403, body=b"<html>")

            with pytest.raises(NanoKVMPermissionError) as exc_info:
                async for _frame in client.mjpeg_stream():
                    pass

            assert exc_info.value.status == 403
            assert exc_info.value.method == "GET"
            assert exc_info.value.path == "/stream/mjpeg"


async def test_mjpeg_401_invalidates_session() -> None:
    """An expired MJPEG session is classified and cleared like JSON requests."""
    async with NanoKVMClient(_BASE_URL, token="expired-token") as client:
        with aioresponses() as mocked:
            mocked.get(f"{_BASE_URL}stream/mjpeg", status=401, body=b'"unauthorized"')

            with pytest.raises(NanoKVMNotAuthenticatedError):
                async for _frame in client.mjpeg_stream():
                    pass

            assert client.token is None
