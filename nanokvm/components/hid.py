"""Internal HID operations for the NanoKVM session."""

from __future__ import annotations

import asyncio
import math

from aiohttp import hdrs

from ..compatibility import require_application_version, require_hardware
from ..exceptions import NanoKVMNotSupportedError
from ..models.common import (
    AddShortcutReq,
    DeleteShortcutReq,
    GetHidModeRsp,
    GetKeyboardLedStatusRsp,
    GetLeaderKeyRsp,
    GetMouseJigglerRsp,
    GetShortcutsRsp,
    HidMode,
    HWFamily,
    KeyCode,
    KeyModifier,
    MouseJigglerMode,
    PasteReq,
    SetHidModeReq,
    SetLeaderKeyReq,
    SetMouseJigglerReq,
    ShortcutKey,
)
from .session import Controller

PASTE_CHAR_MAP = set(
    "\t\n !\"#$%&'()*+,-./0123456789"
    ":;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
)

_KEYBOARD_EVENT = 1
_KEYBOARD_REPORT_KEYS = 6


class HidController(Controller):
    """Implement HID operations behind the public client facade."""

    @require_application_version(non_pro="2.2.6")
    async def get_mouse_jiggler_state(self) -> GetMouseJigglerRsp:
        """Get the mouse jiggler state."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/mouse-jiggler",
            response_model=GetMouseJigglerRsp,
        )

    @require_application_version(non_pro="2.2.6")
    async def set_mouse_jiggler_state(
        self, enabled: bool, mode: MouseJigglerMode
    ) -> None:
        """Set the mouse jiggler state."""
        current = await self.get_mouse_jiggler_state()
        if current.enabled == enabled and (not enabled or current.mode == mode):
            return

        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/mouse-jiggler/",
            data=SetMouseJigglerReq(enabled=enabled, mode=mode),
        )

    @require_application_version(non_pro="2.2.5")
    async def get_hid_mode(self) -> GetHidModeRsp:
        """Get the current HID mode."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/hid/mode",
            response_model=GetHidModeRsp,
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.0")
    async def get_keyboard_led_status(self) -> GetKeyboardLedStatusRsp:
        """Get host keyboard LED state on non-Pro firmware 2.5.0 and newer.

        When ``known`` is false, the host has not reported its LED state yet.
        """
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/hid/leds",
            response_model=GetKeyboardLedStatusRsp,
        )

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def get_shortcuts(self) -> GetShortcutsRsp:
        """Get configured custom HID shortcuts."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/hid/shortcuts",
            response_model=GetShortcutsRsp,
        )

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def add_shortcut(self, keys: list[ShortcutKey]) -> None:
        """Add a custom HID shortcut."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/hid/shortcut",
            data=AddShortcutReq(keys=keys),
        )

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def delete_shortcut(self, shortcut_id: str) -> None:
        """Delete a custom HID shortcut."""
        await self._session.api_request_json(
            hdrs.METH_DELETE,
            "/hid/shortcut",
            data=DeleteShortcutReq(id=shortcut_id),
        )

    @require_application_version(non_pro="2.3.4", pro="1.2.12")
    async def get_leader_key(self) -> GetLeaderKeyRsp:
        """Get the configured shortcut leader key."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/hid/shortcut/leader-key",
            response_model=GetLeaderKeyRsp,
        )

    @require_application_version(non_pro="2.3.4", pro="1.2.12")
    async def set_leader_key(self, key: str = "") -> None:
        """Set or clear the shortcut leader key."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/hid/shortcut/leader-key",
            data=SetLeaderKeyReq(key=key),
        )

    @require_application_version(non_pro="2.2.5")
    async def set_hid_mode(self, mode: HidMode) -> None:
        """Set the HID mode (requires reboot)."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/hid/mode",
            data=SetHidModeReq(mode=mode),
        )

    @require_application_version(pro="1.1.6")
    async def reset_hid(self) -> None:
        """Reset the HID subsystem."""
        await self._session.api_request_json(hdrs.METH_POST, "/hid/reset")

    async def paste_text(self, text: str) -> None:
        """Paste text via HID keyboard simulation."""
        invalid_chars = set(text) - PASTE_CHAR_MAP
        if invalid_chars:
            raise ValueError(f"Invalid characters for paste: {invalid_chars}")
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/hid/paste",
            data=PasteReq(content=text),
        )

    async def key_down(
        self, *keys: KeyCode | int, modifiers: KeyModifier | int = 0
    ) -> None:
        """Hold keys and modifiers down, replacing those held before.

        Args:
            keys: Up to six USB HID key codes, such as ``KeyCode.F12``.
            modifiers: Modifier keys to hold, such as ``KeyModifier.LEFT_CTRL``.

        Before non-Pro 2.3.2 and Pro 1.2.6, the device holds one key at a time
        and applies modifiers only together with a key.
        """
        await self._send_keyboard_report(int(modifiers), [int(key) for key in keys])

    async def key_up(self) -> None:
        """Release all keys and modifiers."""
        await self._send_keyboard_report(0, [])

    async def key_tap(
        self,
        *keys: KeyCode | int,
        modifiers: KeyModifier | int = 0,
        hold: float = 0.05,
    ) -> None:
        """Press keys and modifiers, and release them after ``hold`` seconds."""
        if not math.isfinite(hold) or hold < 0:
            raise ValueError("hold must be a finite, non-negative number of seconds")

        await self.key_down(*keys, modifiers=modifiers)
        try:
            await asyncio.sleep(hold)
        finally:
            # Release even when cancelled, so no key stays held on the host.
            await self.key_up()

    async def _send_keyboard_report(self, modifiers: int, keys: list[int]) -> None:
        """Send a keyboard report in the wire format of the connected device."""
        if len(keys) > _KEYBOARD_REPORT_KEYS:
            raise ValueError("A keyboard report holds at most six keys")
        if not all(0 <= value <= 0xFF for value in (modifiers, *keys)):
            raise ValueError("Key codes and modifiers must be between 0 and 255")

        session = self._session
        if await session.uses_binary_hid_protocol():
            # Boot keyboard report: modifiers, a reserved byte and six key slots.
            report = bytes((modifiers, 0, *keys)).ljust(8, bytes(1))
            await session.send_ws(
                lambda ws: ws.send_bytes(bytes((_KEYBOARD_EVENT,)) + report)
            )
            return

        if len(keys) > 1 or (modifiers and not any(keys)):
            raise NanoKVMNotSupportedError(
                "Holding several keys, or modifiers without a key, requires the "
                "binary keyboard protocol"
            )
        # The legacy server combines the four modifier fields into one byte.
        message = [_KEYBOARD_EVENT, keys[0] if keys else 0, modifiers, 0, 0, 0]
        await session.send_ws(lambda ws: ws.send_json(message))
