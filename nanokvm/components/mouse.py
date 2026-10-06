"""Internal WebSocket and mouse transport for the NanoKVM session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging

import aiohttp

from ..exceptions import NanoKVMNotSupportedError
from ..models.common import MouseButton
from .session import Controller

_LOGGER = logging.getLogger(__name__)

_BINARY_MOUSE_MIN_NON_PRO_VERSION = "2.3.2"
_BINARY_MOUSE_MIN_PRO_VERSION = "1.2.6"


class MouseController(Controller):
    """Own the mouse protocol and its lazily-created WebSocket transport."""

    async def _uses_binary_mouse_protocol(self) -> bool:
        """Select the mouse wire format supported by the connected device."""
        # Without a token the WebSocket reports the authentication error, so
        # the current protocol stays the default when detection is impossible.
        return await self._session.application_version_at_least(
            non_pro=_BINARY_MOUSE_MIN_NON_PRO_VERSION,
            pro=_BINARY_MOUSE_MIN_PRO_VERSION,
        )

    @staticmethod
    def _clamp(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(maximum, value))

    @classmethod
    def _relative_value(cls, value: float) -> int:
        return cls._clamp(round(value * 127), -127, 127)

    @classmethod
    def _absolute_value(cls, value: float) -> int:
        return cls._clamp(round(max(0.0, min(1.0, value)) * 32767), 0, 32767)

    def _absolute_report(self, wheel: int = 0) -> bytes:
        session = self._session
        x, y = session._mouse_abs_position
        return bytes(
            (
                session._mouse_buttons,
                x & 0xFF,
                (x >> 8) & 0xFF,
                y & 0xFF,
                (y >> 8) & 0xFF,
                self._clamp(wheel, -127, 127) & 0xFF,
            )
        )

    def _relative_report(self, dx: int = 0, dy: int = 0, wheel: int = 0) -> bytes:
        session = self._session
        return bytes(
            (
                session._mouse_buttons,
                self._clamp(dx, -127, 127) & 0xFF,
                self._clamp(dy, -127, 127) & 0xFF,
                self._clamp(wheel, -127, 127) & 0xFF,
            )
        )

    def _report_for_current_mode(self, *, wheel: int = 0) -> bytes:
        """Build a button or wheel report for the active mouse mode."""
        if self._session._mouse_mode == "absolute":
            return self._absolute_report(wheel)
        return self._relative_report(wheel=wheel)

    async def _send_ws(
        self,
        send: Callable[[aiohttp.ClientWebSocketResponse], Awaitable[None]],
    ) -> None:
        """Send one mouse message and invalidate the connection on failure."""
        ws = await self._session.get_ws()
        try:
            await send(ws)
        except (aiohttp.ClientConnectionError, ConnectionError, RuntimeError):
            await self._session.invalidate_ws(ws)
            raise

    async def _send_mouse_report(self, report: bytes) -> None:
        """Send a binary NanoKVM mouse event and HID report."""
        await self._send_ws(lambda ws: ws.send_bytes(bytes((2,)) + report))

    async def _send_legacy_mouse_event(
        self, event_type: int, button_state: int, x: float, y: float
    ) -> None:
        """Send a mouse event using the pre-2.3.2 JSON wire format."""
        if event_type == 2:
            x_value = int(x * 32768)
            y_value = int(y * 32768)
        elif event_type == 3:
            x_value = self._relative_value(x)
            y_value = self._relative_value(y)
        elif event_type == 4:
            x_value = 0
            y_value = 1 if y > 0 else -1 if y < 0 else 0
        else:
            x_value = int(x)
            y_value = int(y)

        message = [2, event_type, button_state, x_value, y_value]
        _LOGGER.debug("Sending legacy mouse event: %s", message)
        await self._send_ws(lambda ws: ws.send_json(message))

    async def mouse_move_abs(self, x: float, y: float) -> None:
        """Move mouse to absolute position.

        Args:
            x: X coordinate (0.0 to 1.0, left to right).
            y: Y coordinate (0.0 to 1.0, top to bottom).
        """
        session = self._session
        if not await self._uses_binary_mouse_protocol():
            await self._send_legacy_mouse_event(2, 0, x, y)
            return

        session._mouse_mode = "absolute"
        session._mouse_abs_position = (self._absolute_value(x), self._absolute_value(y))
        await self._send_mouse_report(self._absolute_report())

    async def mouse_move_rel(self, dx: float, dy: float) -> None:
        """Move mouse relative to current position.

        Args:
            dx: Horizontal movement (-1.0 to 1.0).
            dy: Vertical movement (-1.0 to 1.0).
        """
        session = self._session
        if not await self._uses_binary_mouse_protocol():
            await self._send_legacy_mouse_event(3, 0, dx, dy)
            return

        session._mouse_mode = "relative"
        await self._send_mouse_report(
            self._relative_report(self._relative_value(dx), self._relative_value(dy))
        )

    async def mouse_down(self, button: MouseButton = MouseButton.LEFT) -> None:
        """Press a mouse button.

        Args:
            button: Mouse button to press (LEFT, RIGHT, MIDDLE, BACK or FORWARD).

        Back and Forward require the binary mouse protocol.
        """

        session = self._session
        if not await self._uses_binary_mouse_protocol():
            if button in (MouseButton.BACK, MouseButton.FORWARD):
                raise NanoKVMNotSupportedError(
                    "Back and Forward mouse buttons require the binary mouse protocol"
                )
            await self._send_legacy_mouse_event(1, int(button), 0.0, 0.0)
            return

        previous_buttons = session._mouse_buttons
        generation = session._session_generation
        session._mouse_buttons |= int(button)
        pressed_buttons = session._mouse_buttons
        report = self._report_for_current_mode()
        try:
            await self._send_mouse_report(report)
        except BaseException:
            # Do not turn an unsent press into a drag on the next movement, or
            # overwrite state changed by a newer session or another mouse call.
            if (
                session._session_generation == generation
                and session._mouse_buttons == pressed_buttons
            ):
                session._mouse_buttons = previous_buttons
            raise

    async def mouse_up(self) -> None:
        """Release a mouse button.

        The report releases all currently held buttons.
        """
        session = self._session
        if not await self._uses_binary_mouse_protocol():
            await self._send_legacy_mouse_event(0, 0, 0.0, 0.0)
            return

        session._mouse_buttons = 0
        report = self._report_for_current_mode()
        await self._send_mouse_report(report)

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
        if not await self._uses_binary_mouse_protocol():
            await self._send_legacy_mouse_event(4, 0, dx, dy)
            return

        del dx  # NanoKVM's boot mouse report has a single vertical wheel byte.
        wheel = self._relative_value(dy)
        report = self._report_for_current_mode(wheel=wheel)
        await self._send_mouse_report(report)
