"""Internal WebSocket and mouse transport for the NanoKVM client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from http.cookies import SimpleCookie
import logging
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import ClientSession, hdrs

from ..compatibility import _version_at_least
from ..exceptions import (
    NanoKVMNotAuthenticatedError,
    NanoKVMNotSupportedError,
    NanoKVMPermissionError,
)
from ..models.common import HWFamily, MouseButton
from .session import _SESSION_COOKIE_NAME

if TYPE_CHECKING:
    from ..client import NanoKVMClient


_BINARY_MOUSE_MIN_NON_PRO_VERSION = "2.3.2"
_BINARY_MOUSE_MIN_PRO_VERSION = "1.2.6"


class MouseController:
    """Own the mouse protocol and its lazily-created WebSocket transport."""

    def __init__(self, client: NanoKVMClient, logger: logging.Logger) -> None:
        self._client = client
        self._logger = logger

    async def close_ws(self) -> None:
        """Close and forget the current WebSocket connection."""
        client = self._client
        async with client._ws_lock:
            ws = client._ws
            client._ws = None
            if ws is not None and not ws.closed:
                await ws.close()

    async def invalidate_ws(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Forget a failed WebSocket without closing a replacement connection."""
        client = self._client
        async with client._ws_lock:
            if client._ws is ws:
                client._ws = None
            if not ws.closed:
                await ws.close()

    async def get_ws(self) -> aiohttp.ClientWebSocketResponse:
        """Get or create WebSocket connection for mouse events."""
        client = self._client
        generation = client._session_generation
        try:
            async with client._ws_lock:
                if client._ws is not None and not client._ws.closed:
                    return client._ws

                if client._ws is not None:
                    await client._ws.close()
                    client._ws = None

                if not client._token:
                    raise NanoKVMNotAuthenticatedError("Client is not authenticated")

                generation = client._session_generation

                # WebSocket URL uses ws:// or wss:// scheme
                scheme = "ws" if client.url.scheme == "http" else "wss"
                ws_url = client.url.with_scheme(scheme) / "ws"

                assert client._session is not None
                assert client._ssl_config is not None

                # ws_connect cannot override cookies per request. An isolated
                # jar prevents concurrent requests or async tracing callbacks
                # from replacing this handshake's identity. Share the external
                # connector without taking ownership of it.
                if client._session.closed:
                    raise RuntimeError("Session is closed")
                if client._ws_session is None or client._ws_session.closed:
                    client._ws_session = ClientSession(
                        connector=client._session.connector,
                        connector_owner=False,
                        cookie_jar=aiohttp.DummyCookieJar(),
                        auth=client._session.auth,
                        trust_env=client._session.trust_env,
                        trace_configs=client._session.trace_configs,
                        skip_auto_headers=client._session.skip_auto_headers,
                        timeout=aiohttp.ClientTimeout(total=client._request_timeout),
                    )

                headers = client._session.headers.copy()
                cookies = SimpleCookie()
                cookies.load(headers.get(hdrs.COOKIE, ""))
                cookies.load(
                    {
                        name: cookie.value
                        for name, cookie in client._session.cookie_jar.filter_cookies(
                            ws_url
                        ).items()
                    }
                )
                cookies[_SESSION_COOKIE_NAME] = client._token
                headers[hdrs.COOKIE] = cookies.output(header="", sep=";").strip()
                client._clear_session_cookies()
                client._ws = await client._ws_session.ws_connect(
                    str(ws_url),
                    headers=headers,
                    ssl=client._ssl_config,
                )
                client._clear_session_cookies()
                return client._ws
        except aiohttp.ClientResponseError as err:
            method = getattr(err.request_info, "method", hdrs.METH_GET)
            if err.status == 401:
                # _clear_local_session acquires _ws_lock, so do this after the
                # lock above has been released to avoid a self-deadlock.
                await client._clear_local_session(expected_generation=generation)
                raise NanoKVMNotAuthenticatedError(
                    "NanoKVM session is no longer authenticated"
                ) from err
            if err.status == 403:
                raise NanoKVMPermissionError(
                    status=err.status,
                    method=method,
                    path="/ws",
                ) from err
            raise

    async def uses_binary_mouse_protocol(self) -> bool:
        """Select the mouse wire format supported by the connected device."""
        client = self._client
        if client._hw_version is None:
            if client._token is None:
                # The WebSocket will report the authentication error below.
                # Keep the current protocol as the safe default when version
                # detection is not possible.
                return True
            await client.detect_hardware()

        if client._application_version is None:
            if client._token is None:
                return True
            await client.detect_versions()

        minimum_version = (
            _BINARY_MOUSE_MIN_PRO_VERSION
            if client._is_hardware_family(HWFamily.PRO)
            else _BINARY_MOUSE_MIN_NON_PRO_VERSION
        )
        return client._application_version is None or _version_at_least(
            client._application_version, minimum_version
        )

    @staticmethod
    def clamp(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(maximum, value))

    @classmethod
    def relative_value(cls, value: float) -> int:
        return cls.clamp(round(value * 127), -127, 127)

    @classmethod
    def absolute_value(cls, value: float) -> int:
        return cls.clamp(round(max(0.0, min(1.0, value)) * 32767), 0, 32767)

    def absolute_report(self, wheel: int = 0) -> bytes:
        client = self._client
        x, y = client._mouse_abs_position
        return bytes(
            (
                client._mouse_buttons,
                x & 0xFF,
                (x >> 8) & 0xFF,
                y & 0xFF,
                (y >> 8) & 0xFF,
                self.clamp(wheel, -127, 127) & 0xFF,
            )
        )

    def relative_report(self, dx: int = 0, dy: int = 0, wheel: int = 0) -> bytes:
        client = self._client
        return bytes(
            (
                client._mouse_buttons,
                self.clamp(dx, -127, 127) & 0xFF,
                self.clamp(dy, -127, 127) & 0xFF,
                self.clamp(wheel, -127, 127) & 0xFF,
            )
        )

    def report_for_current_mode(self, *, wheel: int = 0) -> bytes:
        """Build a button or wheel report for the active mouse mode."""
        if self._client._mouse_mode == "absolute":
            return self.absolute_report(wheel)
        return self.relative_report(wheel=wheel)

    async def send_ws(
        self,
        send: Callable[[aiohttp.ClientWebSocketResponse], Awaitable[None]],
    ) -> None:
        """Send one mouse message and invalidate the connection on failure."""
        ws = await self.get_ws()
        try:
            await send(ws)
        except (aiohttp.ClientConnectionError, ConnectionError, RuntimeError):
            await self.invalidate_ws(ws)
            raise

    async def send_mouse_report(self, report: bytes) -> None:
        """Send a binary NanoKVM mouse event and HID report."""
        await self.send_ws(lambda ws: ws.send_bytes(bytes((2,)) + report))

    async def send_legacy_mouse_event(
        self, event_type: int, button_state: int, x: float, y: float
    ) -> None:
        """Send a mouse event using the pre-2.3.2 JSON wire format."""
        if event_type == 2:
            x_value = int(x * 32768)
            y_value = int(y * 32768)
        elif event_type == 3:
            x_value = self.relative_value(x)
            y_value = self.relative_value(y)
        elif event_type == 4:
            x_value = 0
            y_value = 1 if y > 0 else -1 if y < 0 else 0
        else:
            x_value = int(x)
            y_value = int(y)

        message = [2, event_type, button_state, x_value, y_value]
        self._logger.debug("Sending legacy mouse event: %s", message)
        await self.send_ws(lambda ws: ws.send_json(message))

    async def mouse_move_abs(self, x: float, y: float) -> None:
        """Move mouse to absolute position.

        Args:
            x: X coordinate (0.0 to 1.0, left to right).
            y: Y coordinate (0.0 to 1.0, top to bottom).
        """
        client = self._client
        if not await self.uses_binary_mouse_protocol():
            await self.send_legacy_mouse_event(2, 0, x, y)
            return

        client._mouse_mode = "absolute"
        client._mouse_abs_position = (self.absolute_value(x), self.absolute_value(y))
        await self.send_mouse_report(self.absolute_report())

    async def mouse_move_rel(self, dx: float, dy: float) -> None:
        """Move mouse relative to current position.

        Args:
            dx: Horizontal movement (-1.0 to 1.0).
            dy: Vertical movement (-1.0 to 1.0).
        """
        client = self._client
        if not await self.uses_binary_mouse_protocol():
            await self.send_legacy_mouse_event(3, 0, dx, dy)
            return

        client._mouse_mode = "relative"
        await self.send_mouse_report(
            self.relative_report(self.relative_value(dx), self.relative_value(dy))
        )

    async def mouse_down(self, button: MouseButton = MouseButton.LEFT) -> None:
        """Press a mouse button.

        Args:
            button: Mouse button to press (LEFT, RIGHT, MIDDLE, BACK or FORWARD).

        Back and Forward require the binary mouse protocol.
        """

        client = self._client
        if not await self.uses_binary_mouse_protocol():
            if button in (MouseButton.BACK, MouseButton.FORWARD):
                raise NanoKVMNotSupportedError(
                    "Back and Forward mouse buttons require the binary mouse protocol"
                )
            await self.send_legacy_mouse_event(1, int(button), 0.0, 0.0)
            return

        previous_buttons = client._mouse_buttons
        generation = client._session_generation
        client._mouse_buttons |= int(button)
        pressed_buttons = client._mouse_buttons
        report = self.report_for_current_mode()
        try:
            await self.send_mouse_report(report)
        except BaseException:
            # Do not turn an unsent press into a drag on the next movement, or
            # overwrite state changed by a newer session or another mouse call.
            if (
                client._session_generation == generation
                and client._mouse_buttons == pressed_buttons
            ):
                client._mouse_buttons = previous_buttons
            raise

    async def mouse_up(self) -> None:
        """Release a mouse button.

        The report releases all currently held buttons.
        """
        client = self._client
        if not await self.uses_binary_mouse_protocol():
            await self.send_legacy_mouse_event(0, 0, 0.0, 0.0)
            return

        client._mouse_buttons = 0
        report = self.report_for_current_mode()
        await self.send_mouse_report(report)

    async def mouse_click(
        self,
        button: MouseButton = MouseButton.LEFT,
        x: float | None = None,
        y: float | None = None,
    ) -> None:
        """Click a mouse button at the current position or coordinates.

        Args:
            button: Mouse button to click (LEFT, RIGHT, MIDDLE, BACK or FORWARD).
            x: Optional X coordinate (0.0 to 1.0) for absolute positioning.
            y: Optional Y coordinate (0.0 to 1.0) for absolute positioning.

        Both coordinates must be provided to reposition the mouse before clicking.
        """
        if x is not None and y is not None:
            await self.mouse_move_abs(x, y)
            await asyncio.sleep(0.05)

        pressed = False
        try:
            await self.mouse_down(button)
            pressed = True
            await asyncio.sleep(0.05)
        finally:
            if pressed:
                # Always release after a successful press, including cancellation.
                await self.mouse_up()

    async def mouse_scroll(self, dx: float, dy: float) -> None:
        """Scroll the mouse wheel.

        Args:
            dx: Horizontal scroll amount (-1.0 to 1.0), ignored by the binary
                mouse protocol.
            dy: Vertical scroll amount (-1.0 to 1.0); positive scrolls up and
                negative scrolls down.
        """
        if not await self.uses_binary_mouse_protocol():
            await self.send_legacy_mouse_event(4, 0, dx, dy)
            return

        del dx  # NanoKVM's boot mouse report has a single vertical wheel byte.
        wheel = self.relative_value(dy)
        report = self.report_for_current_mode(wheel=wheel)
        await self.send_mouse_report(report)
