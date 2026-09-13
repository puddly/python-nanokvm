"""Concurrent responses must only invalidate the session that sent them."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aioresponses import CallbackResult, aioresponses
from multidict import CIMultiDict, CIMultiDictProxy
import pytest
import yarl

from nanokvm.client import NanoKVMClient, NanoKVMNotAuthenticatedError
from nanokvm.models import HWVersion

_URL = "http://kvm.local/api/"


@pytest.mark.parametrize("transport", ["json", "form", "mjpeg"])
@pytest.mark.parametrize("new_token", ["new-session", "old-session"])
async def test_delayed_401_does_not_clear_replacement_session(
    transport: str,
    new_token: str,
) -> None:
    """Even an identical reissued token belongs to a new login generation."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_401(*args: object, **kwargs: object) -> CallbackResult:
        started.set()
        await release.wait()
        return CallbackResult(status=401, body='"unauthorized"')

    async with NanoKVMClient(_URL, token="old-session") as client:

        async def request() -> None:
            if transport == "json":
                await client.get_account()
            elif transport == "form":
                await client._api_request_form(
                    "POST", "/upload", data=aiohttp.FormData()
                )
            else:
                async for _ in client.mjpeg_stream():
                    pass

        with aioresponses() as mocked:
            if transport == "json":
                mocked.get(f"{_URL}auth/account", callback=delayed_401)
            elif transport == "form":
                mocked.post(f"{_URL}upload", callback=delayed_401)
            else:
                mocked.get(f"{_URL}stream/mjpeg", callback=delayed_401)
            mocked.post(
                f"{_URL}auth/login",
                payload={"code": 0, "msg": "ok", "data": {"token": new_token}},
            )
            mocked.get(
                f"{_URL}vm/hardware",
                payload={"code": 0, "msg": "ok", "data": {"version": "PCIE"}},
            )
            async with asyncio.timeout(2):
                pending = asyncio.create_task(request())
                await started.wait()
                try:
                    await client.authenticate("synthetic-user", "synthetic-password")
                finally:
                    release.set()
                with pytest.raises(NanoKVMNotAuthenticatedError):
                    await pending
            assert client.token == new_token


async def test_websocket_401_waiting_for_cleanup_cannot_clear_new_login() -> None:
    """Model another login winning the lock before handshake cleanup runs."""
    cleanup_started = asyncio.Event()
    release = asyncio.Event()
    error = aiohttp.WSServerHandshakeError(
        aiohttp.RequestInfo(
            yarl.URL(_URL + "ws"), "GET", CIMultiDictProxy(CIMultiDict())
        ),
        (),
        status=401,
    )
    async with NanoKVMClient(_URL, token="old-session") as client:
        original_clear = client._clear_local_session

        async def delayed_clear(*args: Any, **kwargs: Any) -> None:
            if asyncio.current_task() is pending:
                cleanup_started.set()
                await release.wait()
            await original_clear(*args, **kwargs)

        with (
            patch.object(client, "_clear_local_session", side_effect=delayed_clear),
            patch("aiohttp.ClientSession.ws_connect", AsyncMock(side_effect=error)),
            aioresponses() as mocked,
        ):
            mocked.post(
                f"{_URL}auth/login",
                payload={"code": 0, "msg": "ok", "data": {"token": "new-session"}},
            )
            mocked.get(
                f"{_URL}vm/hardware",
                payload={"code": 0, "msg": "ok", "data": {"version": "PCIE"}},
            )
            pending = asyncio.create_task(client._get_ws())
            async with asyncio.timeout(2):
                await cleanup_started.wait()
                try:
                    await client.authenticate("synthetic-user", "synthetic-password")
                finally:
                    release.set()
                with pytest.raises(NanoKVMNotAuthenticatedError):
                    await pending
            assert client.token == "new-session"


async def test_session_cleanup_does_not_close_new_websocket_while_old_one_closes() -> (
    None
):
    """Closing an old socket must not hold up or dispose of a replacement."""
    closing = asyncio.Event()
    finish_close = asyncio.Event()

    async def close_old() -> None:
        closing.set()
        await finish_close.wait()

    async with NanoKVMClient(_URL, token="old-session") as client:
        old_ws = AsyncMock(closed=False)
        old_ws.close.side_effect = close_old
        client._ws = old_ws
        cleanup = asyncio.create_task(client._clear_local_session())
        async with asyncio.timeout(2):
            await closing.wait()
            try:
                with aioresponses() as mocked:
                    mocked.post(
                        f"{_URL}auth/login",
                        payload={
                            "code": 0,
                            "msg": "ok",
                            "data": {"token": "new-session"},
                        },
                    )
                    mocked.get(
                        f"{_URL}vm/hardware",
                        payload={"code": 0, "msg": "ok", "data": {"version": "PCIE"}},
                    )
                    await client.authenticate("synthetic-user", "synthetic-password")
                new_ws = AsyncMock(closed=False)
                with patch(
                    "aiohttp.ClientSession.ws_connect", AsyncMock(return_value=new_ws)
                ):
                    assert await client._get_ws() is new_ws
            finally:
                finish_close.set()
                await cleanup
            assert client.token == "new-session"
            assert client._ws is new_ws
            new_ws.close.assert_not_awaited()


@pytest.mark.parametrize("operation", ["logout", "password"])
async def test_delayed_session_ending_response_keeps_new_login(operation: str) -> None:
    """Successful logout/password responses belong to the sending session too."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_success(*args: object, **kwargs: object) -> CallbackResult:
        started.set()
        await release.wait()
        return CallbackResult(payload={"code": 0, "msg": "ok", "data": None})

    async with NanoKVMClient(_URL, token="old-session") as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = "2.5.1"
        with aioresponses() as mocked:
            mocked.get(
                f"{_URL}auth/account",
                payload={
                    "code": 0,
                    "msg": "ok",
                    "data": {"username": "synthetic-user"},
                },
            )
            mocked.post(f"{_URL}auth/{operation}", callback=delayed_success)
            mocked.post(
                f"{_URL}auth/login",
                payload={"code": 0, "msg": "ok", "data": {"token": "new-session"}},
            )
            mocked.get(
                f"{_URL}vm/hardware",
                payload={"code": 0, "msg": "ok", "data": {"version": "PCIE"}},
            )
            pending = asyncio.create_task(
                client.logout()
                if operation == "logout"
                else client.change_password(
                    "synthetic-user", "new-password", current_password="old-password"
                )
            )
            async with asyncio.timeout(2):
                await started.wait()
                try:
                    await client.authenticate("synthetic-user", "synthetic-password")
                finally:
                    release.set()
                await pending
            assert client.token == "new-session"
