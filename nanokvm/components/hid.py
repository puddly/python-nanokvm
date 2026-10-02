"""Internal HID operations for the NanoKVM client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import hdrs

from ..models.common import (
    AddShortcutReq,
    DeleteShortcutReq,
    GetHidModeRsp,
    GetKeyboardLedStatusRsp,
    GetLeaderKeyRsp,
    GetMouseJigglerRsp,
    GetShortcutsRsp,
    HidMode,
    MouseJigglerMode,
    PasteReq,
    SetHidModeReq,
    SetLeaderKeyReq,
    SetMouseJigglerReq,
    ShortcutKey,
)

if TYPE_CHECKING:
    from ..client import NanoKVMClient


PASTE_CHAR_MAP = set(
    "\t\n !\"#$%&'()*+,-./0123456789"
    ":;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
)


class HidController:
    """Implement HID operations behind the public client facade."""

    def __init__(self, client: NanoKVMClient) -> None:
        self._client = client

    async def get_mouse_jiggler_state(self) -> GetMouseJigglerRsp:
        """Get the mouse jiggler state."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/vm/mouse-jiggler",
            response_model=GetMouseJigglerRsp,
        )

    async def set_mouse_jiggler_state(
        self, enabled: bool, mode: MouseJigglerMode
    ) -> None:
        """Set the mouse jiggler state."""
        current = await self._client.get_mouse_jiggler_state()
        if current.enabled == enabled and (not enabled or current.mode == mode):
            return

        await self._client._api_request_json(
            hdrs.METH_POST,
            "/vm/mouse-jiggler/",
            data=SetMouseJigglerReq(enabled=enabled, mode=mode),
        )

    async def get_hid_mode(self) -> GetHidModeRsp:
        """Get the current HID mode."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/hid/mode",
            response_model=GetHidModeRsp,
        )

    async def get_keyboard_led_status(self) -> GetKeyboardLedStatusRsp:
        """Get host keyboard LED state on non-Pro firmware 2.5.0 and newer.

        When ``known`` is false, the host has not reported its LED state yet.
        """
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/hid/leds",
            response_model=GetKeyboardLedStatusRsp,
        )

    async def get_shortcuts(self) -> GetShortcutsRsp:
        """Get configured custom HID shortcuts."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/hid/shortcuts",
            response_model=GetShortcutsRsp,
        )

    async def add_shortcut(self, keys: list[ShortcutKey]) -> None:
        """Add a custom HID shortcut."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/hid/shortcut",
            data=AddShortcutReq(keys=keys),
        )

    async def delete_shortcut(self, shortcut_id: str) -> None:
        """Delete a custom HID shortcut."""
        await self._client._api_request_json(
            hdrs.METH_DELETE,
            "/hid/shortcut",
            data=DeleteShortcutReq(id=shortcut_id),
        )

    async def get_leader_key(self) -> GetLeaderKeyRsp:
        """Get the configured shortcut leader key."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/hid/shortcut/leader-key",
            response_model=GetLeaderKeyRsp,
        )

    async def set_leader_key(self, key: str = "") -> None:
        """Set or clear the shortcut leader key."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/hid/shortcut/leader-key",
            data=SetLeaderKeyReq(key=key),
        )

    async def set_hid_mode(self, mode: HidMode) -> None:
        """Set the HID mode (requires reboot)."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/hid/mode",
            data=SetHidModeReq(mode=mode),
        )

    async def reset_hid(self) -> None:
        """Reset the HID subsystem."""
        await self._client._api_request_json(hdrs.METH_POST, "/hid/reset")

    async def paste_text(self, text: str) -> None:
        """Paste text via HID keyboard simulation."""
        invalid_chars = set(text) - PASTE_CHAR_MAP
        if invalid_chars:
            raise ValueError(f"Invalid characters for paste: {invalid_chars}")
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/hid/paste",
            data=PasteReq(content=text),
        )
