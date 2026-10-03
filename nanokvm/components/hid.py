"""Internal HID operations for the NanoKVM session."""

from __future__ import annotations

from aiohttp import hdrs

from ..compatibility import require_application_version, require_hardware
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

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
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
