"""Tests for remote keyboard LED status."""

from datetime import datetime, timezone

from aioresponses import aioresponses
import pytest

from nanokvm.client import NanoKVMClient, NanoKVMPermissionError
from nanokvm.models import GetKeyboardLedStatusRsp, HWVersion

_BASE_URL = "http://localhost:8888/api/"


async def test_get_keyboard_led_status_parses_the_device_response() -> None:
    """Read all keyboard LED states and the host timestamp."""
    async with NanoKVMClient(
        _BASE_URL,
        token="synthetic-token",
    ) as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = "2.5.1"
        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}hid/leds",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "numLock": True,
                        "capsLock": False,
                        "scrollLock": True,
                        "known": True,
                        "updatedAt": "2026-09-13T12:34:56Z",
                    },
                },
            )

            status = await client.get_keyboard_led_status()

    assert status.num_lock is True
    assert status.caps_lock is False
    assert status.scroll_lock is True
    assert status.known is True
    assert status.updated_at == datetime(2026, 9, 13, 12, 34, 56, tzinfo=timezone.utc)


def test_keyboard_led_status_preserves_unknown_state() -> None:
    """The known flag distinguishes an unknown host state from all-off."""
    status = GetKeyboardLedStatusRsp.model_validate(
        {
            "numLock": False,
            "capsLock": False,
            "scrollLock": False,
            "known": False,
            "updatedAt": None,
        }
    )

    assert status.known is False
    assert status.updated_at is None


async def test_get_keyboard_led_status_preserves_permission_errors() -> None:
    """A forbidden LED query is classified by the shared HTTP error handling."""
    async with NanoKVMClient(
        _BASE_URL,
        token="synthetic-token",
    ) as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = "2.5.1"
        with aioresponses() as mocked:
            mocked.get(f"{_BASE_URL}hid/leds", status=403, body=b'"forbidden"')

            with pytest.raises(NanoKVMPermissionError) as exc_info:
                await client.get_keyboard_led_status()

    assert exc_info.value.status == 403
    assert exc_info.value.method == "GET"
    assert exc_info.value.path == "/hid/leds"
