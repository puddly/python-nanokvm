"""Tests for remote keyboard LED status."""

from datetime import datetime, timezone

from aioresponses import aioresponses
import pytest

from nanokvm.client import (
    NanoKVMClient,
    NanoKVMNotSupportedError,
    NanoKVMPermissionError,
)
from nanokvm.models import GetKeyboardLedStatusRsp, HWVersion

from .common import mark_detected

_BASE_URL = "http://localhost:8888/api/"


@pytest.mark.parametrize("hardware", [HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE])
@pytest.mark.parametrize("application", ["2.5.0", "2.5.1"])
async def test_get_keyboard_led_status_parses_the_device_response(
    hardware: HWVersion, application: str
) -> None:
    """Read all keyboard LED states and the host timestamp."""
    async with NanoKVMClient(
        _BASE_URL,
        token="synthetic-token",
    ) as client:
        mark_detected(client, hardware, application)
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


@pytest.mark.parametrize(
    ("hardware", "application"),
    [
        (HWVersion.ALPHA, "2.4.3"),
        (HWVersion.BETA, "2.4.3"),
        (HWVersion.PCIE, "2.4.3"),
        (HWVersion.PRO, "1.2.15"),
    ],
)
async def test_keyboard_led_status_rejects_unsupported_devices_without_request(
    hardware: HWVersion, application: str
) -> None:
    """Unsupported devices fail locally instead of requesting a missing route."""
    async with NanoKVMClient(_BASE_URL, token="synthetic-token") as client:
        mark_detected(client, hardware, application)
        with aioresponses() as mocked:
            with pytest.raises(NanoKVMNotSupportedError):
                await client.get_keyboard_led_status()
            assert not mocked.requests


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


def test_keyboard_led_status_normalizes_empty_update_timestamp() -> None:
    """The firmware uses an empty timestamp before the host reports its state."""
    status = GetKeyboardLedStatusRsp.model_validate(
        {
            "numLock": False,
            "capsLock": False,
            "scrollLock": False,
            "known": False,
            "updatedAt": "",
        }
    )

    assert status.updated_at is None


async def test_get_keyboard_led_status_preserves_permission_errors() -> None:
    """A forbidden LED query is classified by the shared HTTP error handling."""
    async with NanoKVMClient(
        _BASE_URL,
        token="synthetic-token",
    ) as client:
        mark_detected(client, HWVersion.PCIE, "2.5.1")
        with aioresponses() as mocked:
            mocked.get(f"{_BASE_URL}hid/leds", status=403, body=b'"forbidden"')

            with pytest.raises(NanoKVMPermissionError) as exc_info:
                await client.get_keyboard_led_status()

    assert exc_info.value.status == 403
    assert exc_info.value.method == "GET"
    assert exc_info.value.path == "/hid/leds"
