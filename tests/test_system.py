"""Tests for system, virtual device and LED operations."""

from datetime import UTC, datetime

import aiohttp
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMApiError, NanoKVMClient
from nanokvm.models import DiskType, HWVersion, VirtualDevice

from .common import BASE_URL, mark_detected


async def test_get_web_title_returns_default_when_unset(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test the firmware's failed read of an unset title maps to the default."""
    mark_detected(client)

    mocked.get(
        f"{BASE_URL}vm/web-title",
        payload={"code": -1, "msg": "read web title failed", "data": None},
    )

    assert (await client.get_web_title()).title == "NanoKVM"


async def test_get_oled_info_raises_for_pro_invalid_file_content(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test Pro OLED invalid file content follows upstream error semantics."""
    client._session._hw_version = HWVersion.PRO

    mocked.get(
        f"{BASE_URL}vm/oled",
        payload={
            "code": -2,
            "msg": "invalid file content",
            "data": None,
        },
    )

    with pytest.raises(NanoKVMApiError) as exc_info:
        await client.get_oled_info()

    assert exc_info.value.code == -2
    assert exc_info.value.msg == "invalid file content"
    info_url = yarl.URL(f"{BASE_URL}vm/info")
    assert ("GET", info_url) not in mocked.requests


async def test_update_virtual_device_ignores_success_data(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test update_virtual_device handles non-Pro success payloads."""
    mocked.post(
        f"{BASE_URL}vm/device/virtual",
        payload={"code": 0, "msg": "success", "data": {"on": True}},
    )

    await client.update_virtual_device(VirtualDevice.DISK)

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}vm/device/virtual"))]
    assert calls[0].kwargs.get("json") == {"device": "disk"}


async def test_get_virtual_device_status_handles_non_pro_shape(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test virtual device status keeps non-Pro fields intact."""
    mocked.get(
        f"{BASE_URL}vm/device/virtual",
        payload={
            "code": 0,
            "msg": "success",
            "data": {"network": True, "media": False, "disk": True},
        },
    )

    response = await client.get_virtual_device_status()

    assert response.network is True
    assert response.media is False
    assert response.disk is True
    assert response.mounted_disk is None


async def test_get_virtual_device_status_handles_pro_shape(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test Pro virtual device status exposes Pro fields without fake non-Pro disk."""
    client._session._hw_version = HWVersion.PRO

    mocked.get(
        f"{BASE_URL}vm/device/virtual",
        payload={
            "code": 0,
            "msg": "success",
            "data": {
                "isNetworkEnabled": True,
                "isMicEnabled": True,
                "mountedDisk": "emmc",
                "isSdCardExist": True,
                "isEmmcExist": True,
            },
        },
    )

    response = await client.get_virtual_device_status()

    assert response.network is True
    assert response.mic is True
    assert response.mounted_disk == "emmc"
    assert response.is_sd_card_exist is True
    assert response.is_emmc_exist is True
    assert response.media is None
    assert response.disk is None


async def test_update_virtual_device_sends_pro_disk_type(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test Pro disk virtual device requests include the disk type."""
    client._session._hw_version = HWVersion.PRO

    mocked.post(
        f"{BASE_URL}vm/device/virtual",
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.update_virtual_device(
        VirtualDevice.DISK,
        disk_type=DiskType.EMMC,
    )

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}vm/device/virtual"))]
    assert calls[0].kwargs.get("json") == {
        "device": "disk",
        "type": "emmc",
    }


async def test_set_led_strip_partial_preserves_current_config(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test partial LED updates post a complete Pro LED configuration."""
    client._session._hw_version = HWVersion.PRO

    mocked.get(
        f"{BASE_URL}vm/ledstrip/get",
        payload={
            "code": 0,
            "msg": "success",
            "data": {
                "on": True,
                "hor": 8,
                "ver": 6,
                "brightness": 25,
            },
        },
    )
    mocked.post(
        f"{BASE_URL}vm/ledstrip/set",
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.set_led_strip(brightness=50)

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}vm/ledstrip/set"))]
    assert calls[0].kwargs.get("json") == {
        "on": True,
        "hor": 8,
        "ver": 6,
        "brightness": 50,
    }


async def test_get_time_reads_first_stream_event(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test get_time parses the first event of the Pro clock stream."""
    mark_detected(client, HWVersion.PRO, "1.2.15")
    mocked.get(
        f"{BASE_URL}vm/time",
        body="event:message\ndata:1791944949000\n\nevent:message\ndata:1\n\n",
    )

    assert await client.get_time() == datetime(2026, 10, 14, 2, 29, 9, tzinfo=UTC)


async def test_set_tls_treats_restart_disconnect_as_success(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test set_tls succeeds when the device restarts before answering."""
    mark_detected(client, HWVersion.PCIE, "2.5.2")
    mocked.post(f"{BASE_URL}vm/tls", exception=aiohttp.ServerDisconnectedError())

    await client.set_tls(True)

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}vm/tls"))]
    assert calls[0].kwargs["json"] == {"enabled": True}
