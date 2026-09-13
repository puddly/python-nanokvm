"""Concurrent authentication must preserve the identity of each operation."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from aiohttp import web
import pytest

from nanokvm.client import NanoKVMClient, NanoKVMNotAuthenticatedError
from nanokvm.models import HWVersion


def _ok(data: object = None) -> web.Response:
    return web.json_response({"code": 0, "msg": "ok", "data": data})


@asynccontextmanager
async def _server(app: web.Application) -> AsyncIterator[str]:
    async def hardware(request: web.Request) -> web.Response:
        return _ok({"version": "PCIE"})

    app.router.add_get("/api/vm/hardware", hardware)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield f"http://localhost:{runner.addresses[0][1]}/api/"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("replacement", ["logout", "login"])
@pytest.mark.parametrize("old_code", [0, -2])
async def test_superseded_login_cannot_restore_identity_or_retry(
    replacement: str, old_code: int
) -> None:
    """A delayed success or credential error cannot undo a newer transition."""
    started, release = asyncio.Event(), asyncio.Event()
    attempts: list[str] = []

    async def login(request: web.Request) -> web.Response:
        username = (await request.json())["username"]
        attempts.append(username)
        if username == "alice":
            started.set()
            await release.wait()
            if old_code != 0:
                return web.json_response({"code": old_code, "msg": "invalid"})
        response = _ok()
        response.set_cookie("nano-kvm-token", username)
        return response

    async def account(request: web.Request) -> web.Response:
        return _ok({"username": request.cookies["nano-kvm-token"]})

    app = web.Application()
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/auth/account", account)
    async with _server(app) as url, NanoKVMClient(url) as client:
        async with asyncio.timeout(2):
            pending = asyncio.create_task(client.authenticate("alice", "password"))
            await started.wait()
            try:
                if replacement == "logout":
                    await client.logout()
                else:
                    await client.authenticate("bob", "password")
            finally:
                release.set()
            with pytest.raises(NanoKVMNotAuthenticatedError, match="Session changed"):
                await pending
        assert attempts.count("alice") == 1
        if replacement == "logout":
            assert client.token is None
        else:
            assert (await client.get_account()).username == "bob"


async def test_password_change_cannot_switch_accounts_during_account_lookup() -> None:
    """The account check and password POST must refer to the same identity."""
    started, release = asyncio.Event(), asyncio.Event()
    changed: list[str] = []
    passwords = {"alice": "shared-password", "bob": "shared-password"}

    async def login(request: web.Request) -> web.Response:
        response = _ok()
        response.set_cookie("nano-kvm-token", "bob")
        return response

    async def account(request: web.Request) -> web.Response:
        username = request.cookies["nano-kvm-token"]
        started.set()
        await release.wait()
        return _ok({"username": username})

    async def password(request: web.Request) -> web.Response:
        username = request.cookies["nano-kvm-token"]
        data = await request.json()
        if passwords[username] != data["currentPassword"]:
            return web.json_response({"code": -3, "msg": "incorrect password"})
        passwords[username] = data["password"]
        changed.append(username)
        return _ok()

    app = web.Application()
    app.router.add_get("/api/auth/account", account)
    app.router.add_post("/api/auth/login", login)
    app.router.add_post("/api/auth/password", password)
    async with _server(app) as url, NanoKVMClient(url, token="alice") as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = "2.5.1"
        with patch(
            "nanokvm.client.obfuscate_password", side_effect=lambda value: value
        ):
            async with asyncio.timeout(2):
                pending = asyncio.create_task(
                    client.change_password(
                        "alice", "new-password", current_password="shared-password"
                    )
                )
                await started.wait()
                try:
                    await client.authenticate("bob", "shared-password")
                finally:
                    release.set()
                with pytest.raises(
                    NanoKVMNotAuthenticatedError, match="Session changed"
                ):
                    await pending
        assert changed == []
        assert passwords == {"alice": "shared-password", "bob": "shared-password"}
        assert client.token == "bob"


async def test_login_superseded_while_closing_old_websocket_is_not_sent() -> None:
    """Closing a detached socket can yield while a newer login completes."""
    closing, release = asyncio.Event(), asyncio.Event()
    attempts: list[str] = []

    async def close() -> None:
        closing.set()
        await release.wait()

    async def login(request: web.Request) -> web.Response:
        username = (await request.json())["username"]
        attempts.append(username)
        return _ok({"token": username})

    app = web.Application()
    app.router.add_post("/api/auth/login", login)
    async with _server(app) as url, NanoKVMClient(url, token="old-session") as client:
        old_ws = AsyncMock(closed=False)
        old_ws.close.side_effect = close
        client._ws = old_ws
        async with asyncio.timeout(2):
            pending = asyncio.create_task(client.authenticate("alice", "password"))
            await closing.wait()
            try:
                await client.authenticate("bob", "password")
            finally:
                release.set()
            with pytest.raises(NanoKVMNotAuthenticatedError, match="Session changed"):
                await pending
        assert attempts == ["bob"]
        assert client.token == "bob"
