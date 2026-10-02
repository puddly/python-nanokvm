"""Exercise session ownership against an actual HTTP/WebSocket server."""

import asyncio
from collections.abc import AsyncIterator
import io
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from PIL import Image
import pytest
import yarl

from nanokvm.client import NanoKVMClient


@pytest.fixture
async def session_server() -> AsyncIterator[str]:
    """Return a local device URL; never send input or modify a real device."""

    async def login(request: web.Request) -> web.Response:
        data = await request.json()
        response = web.json_response({"code": 0, "msg": "ok", "data": None})
        response.set_cookie("nano-kvm-token", data["username"], httponly=True)
        return response

    async def hardware(request: web.Request) -> web.Response:
        return web.json_response({"code": 0, "msg": "ok", "data": {"version": "PCIE"}})

    async def account(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "username": request.cookies.get("nano-kvm-token", "anonymous")
                },
            }
        )

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(request.cookies.get("nano-kvm-token", "anonymous"))
        await ws.send_json(
            {
                "proxy_header": request.headers.get("X-Proxy-Key"),
                "proxy_cookie": request.cookies.get("proxy-session"),
            }
        )
        async for _ in ws:
            pass
        return ws

    async def stream(request: web.Request) -> web.Response:
        image = io.BytesIO()
        width = 2 if request.cookies.get("nano-kvm-token") == "reader" else 3
        Image.new("RGB", (width, 2)).save(image, format="JPEG")
        return web.Response(
            body=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
            + image.getvalue()
            + b"\r\n--frame--\r\n",
            headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"},
        )

    async def logout(request: web.Request) -> web.Response:
        return web.Response(status=503)

    app = web.Application()
    app.router.add_post("/api/auth/login", login)
    app.router.add_post("/api/auth/logout", logout)
    app.router.add_get("/api/vm/hardware", hardware)
    app.router.add_get("/api/auth/account", account)
    app.router.add_get("/api/ws", websocket)
    app.router.add_get("/api/stream/mjpeg", stream)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        yield f"http://localhost:{port}/api/"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("jar_kind", ["normal", "dummy", "ip"])
async def test_shared_http_session_keeps_each_client_identity(
    session_server: str, jar_kind: str
) -> None:
    """A second login must not replace the first client's WebSocket identity."""
    jar = (
        aiohttp.DummyCookieJar()
        if jar_kind == "dummy"
        else aiohttp.CookieJar(unsafe=jar_kind == "ip")
    )
    url = (
        session_server.replace("localhost", "127.0.0.1")
        if jar_kind == "ip"
        else session_server
    )
    async with aiohttp.ClientSession(cookie_jar=jar) as session:
        async with (
            NanoKVMClient(url, session=session) as reader,
            NanoKVMClient(url, session=session) as admin,
        ):
            await reader.authenticate("reader", "synthetic-password")
            await admin.authenticate("admin", "synthetic-password")
            assert (await reader.get_account()).username == "reader"
            ws = await reader._session.get_ws()
            assert await ws.receive_str() == "reader"
            async for frame in reader.mjpeg_stream():
                assert frame.size == (2, 2)
            assert (await admin.get_account()).username == "admin"
        assert not session.closed


@pytest.mark.parametrize("token", ["reader", "disabled"])
async def test_explicit_websocket_token_overrides_stale_cookie(
    session_server: str, token: str
) -> None:
    """Externally supplied tokens also take precedence, including disabled auth."""
    async with aiohttp.ClientSession() as session:
        session.cookie_jar.update_cookies(
            {"nano-kvm-token": "stale"}, yarl.URL(session_server)
        )
        async with NanoKVMClient(
            session_server, session=session, token=token
        ) as client:
            ws = await client._session.get_ws()
            assert await ws.receive_str() == token


async def test_failed_logout_removes_only_device_session_cookies(
    session_server: str,
) -> None:
    """Network failure must not leave implicit credentials in an external jar."""
    async with aiohttp.ClientSession() as session:
        jar = session.cookie_jar
        jar.update_cookies({"preference": "dark"}, yarl.URL(session_server))
        jar.update_cookies(
            {"nano-kvm-token": "other-device"}, yarl.URL("http://other.local/")
        )
        jar.update_cookies(
            {"nano-kvm-token": "other-service"},
            yarl.URL(session_server) / "../../unrelated/page",
        )
        async with NanoKVMClient(
            session_server, session=session, token="reader"
        ) as client:
            jar.update_cookies(
                {"nano-kvm-token": "old-session"}, yarl.URL(session_server)
            )
            with pytest.raises(aiohttp.ClientResponseError):
                await client.logout()
            assert client.token is None
            assert "nano-kvm-token" not in jar.filter_cookies(yarl.URL(session_server))
            assert (
                jar.filter_cookies(yarl.URL(session_server))["preference"].value
                == "dark"
            )
            assert (
                jar.filter_cookies(yarl.URL("http://other.local/"))[
                    "nano-kvm-token"
                ].value
                == "other-device"
            )
            assert (
                jar.filter_cookies(
                    yarl.URL(session_server).with_path("/unrelated/page")
                )["nano-kvm-token"].value
                == "other-service"
            )
        assert not session.closed


async def test_async_traces_cannot_replace_websocket_identity(
    session_server: str,
) -> None:
    """Another login can update the jar while async request tracing is paused."""
    ws_started, cookie_received, release = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    trace = aiohttp.TraceConfig()

    async def start(
        session: aiohttp.ClientSession,
        context: SimpleNamespace,
        params: aiohttp.TraceRequestStartParams,
    ) -> None:
        if params.url.path.endswith("/ws"):
            ws_started.set()
            await cookie_received.wait()

    async def end(
        session: aiohttp.ClientSession,
        context: SimpleNamespace,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        if params.url.path.endswith("/auth/login"):
            cookie_received.set()
            await release.wait()

    trace.on_request_start.append(start)
    trace.on_request_end.append(end)
    async with aiohttp.ClientSession(trace_configs=[trace]) as session:
        session.headers["X-Proxy-Key"] = "synthetic-proxy-key"
        session.cookie_jar.update_cookies(
            {"proxy-session": "synthetic-proxy-cookie"}, yarl.URL(session_server)
        )
        async with (
            NanoKVMClient(session_server, session=session, token="reader") as reader,
            NanoKVMClient(session_server, session=session) as admin,
        ):
            async with asyncio.timeout(2):
                ws_pending = asyncio.create_task(reader._session.get_ws())
                await ws_started.wait()
                login_pending = asyncio.create_task(
                    admin.authenticate("admin", "password")
                )
                try:
                    ws = await ws_pending
                    identity = await ws.receive_str()
                    proxy_state = await ws.receive_json()
                finally:
                    release.set()
                    await login_pending
            assert identity == "reader"
            assert proxy_state == {
                "proxy_header": "synthetic-proxy-key",
                "proxy_cookie": "synthetic-proxy-cookie",
            }
        assert not session.closed
        assert session.connector is not None and not session.connector.closed
        async with session.get(session_server + "vm/hardware") as response:
            assert response.status == 200


async def test_closed_external_session_cannot_open_websocket(
    session_server: str,
) -> None:
    """A closed external connector must never be replaced by an unowned one."""
    async with (
        aiohttp.ClientSession() as session,
        NanoKVMClient(session_server, session=session, token="reader") as client,
    ):
        await session.close()
        try:
            with pytest.raises(RuntimeError, match="Session is closed"):
                await client._session.get_ws()
        finally:
            # Also clean up the accidental connector when testing a regression.
            if (
                client._session._ws_session is not None
                and client._session._ws_session.connector is not None
            ):
                await client._session._ws_session.connector.close()
