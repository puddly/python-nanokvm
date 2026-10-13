"""Tests for HID operations."""

from aioresponses import aioresponses
import yarl

from nanokvm.client import NanoKVMClient
from nanokvm.models import MouseJigglerMode, ShortcutKey

from .common import BASE_URL, mark_detected


async def test_set_mouse_jiggler_state_noops_when_already_disabled(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test disabling mouse jiggler is idempotent."""
    mark_detected(client)

    mocked.get(
        f"{BASE_URL}vm/mouse-jiggler",
        payload={
            "code": 0,
            "msg": "success",
            "data": {"enabled": False, "mode": "relative"},
        },
    )

    await client.set_mouse_jiggler_state(
        enabled=False,
        mode=MouseJigglerMode.RELATIVE,
    )

    post_url = yarl.URL(f"{BASE_URL}vm/mouse-jiggler/")
    assert ("POST", post_url) not in mocked.requests


async def test_shortcut_methods_send_expected_payloads(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test custom shortcut client helpers use the expected endpoints."""
    mark_detected(client, application_version="2.3.4")

    mocked.post(
        f"{BASE_URL}hid/shortcut",
        payload={"code": 0, "msg": "success", "data": None},
    )
    mocked.delete(
        f"{BASE_URL}hid/shortcut",
        payload={"code": 0, "msg": "success", "data": None},
    )
    mocked.post(
        f"{BASE_URL}hid/shortcut/leader-key",
        payload={"code": 0, "msg": "success", "data": None},
    )

    key = ShortcutKey(code="ControlLeft", label="Ctrl")
    await client.add_shortcut([key])
    await client.delete_shortcut("shortcut-1")
    await client.set_leader_key("ControlLeft")

    shortcut_calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}hid/shortcut"))]
    delete_calls = mocked.requests[("DELETE", yarl.URL(f"{BASE_URL}hid/shortcut"))]
    leader_calls = mocked.requests[
        (
            "POST",
            yarl.URL(f"{BASE_URL}hid/shortcut/leader-key"),
        )
    ]
    assert shortcut_calls[0].kwargs.get("json") == {
        "keys": [{"code": "ControlLeft", "label": "Ctrl"}]
    }
    assert delete_calls[0].kwargs.get("json") == {"id": "shortcut-1"}
    assert leader_calls[0].kwargs.get("json") == {"key": "ControlLeft"}
