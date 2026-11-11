"""Tests for video stream settings."""

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMClient
from nanokvm.exceptions import NanoKVMNotSupportedError
from nanokvm.models import HWVersion, ScreenSettingType, StreamMode

from .common import BASE_URL, mark_detected


async def test_set_stream_mode_accepts_existing_string_values(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test set_stream_mode remains source-compatible with valid strings."""
    client._session._hw_version = HWVersion.PRO

    mocked.post(
        f"{BASE_URL}stream/mode",
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.set_stream_mode(StreamMode.H264_DIRECT.value)

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}stream/mode"))]
    assert calls[0].kwargs.get("json") == {"mode": "h264-direct"}


async def test_pause_frame_detect(client: NanoKVMClient, mocked: aioresponses) -> None:
    """Test the pause sends its duration and rejects non-positive ones."""
    mark_detected(client, HWVersion.PCIE)
    url = f"{BASE_URL}stream/mjpeg/detect/stop"
    mocked.post(url, payload={"code": 0, "msg": "success", "data": None})

    await client.pause_frame_detect(3)

    calls = mocked.requests[("POST", yarl.URL(url))]
    assert calls[0].kwargs["json"] == {"duration": 3}
    with pytest.raises(ValueError):
        await client.pause_frame_detect(0)


async def test_set_screen_resolution_removed_in_2_5_4(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test resolution is refused from 2.5.4 and still sent before it."""
    url = f"{BASE_URL}vm/screen"
    mocked.post(url, payload={"code": 0, "msg": "success", "data": None}, repeat=True)

    mark_detected(client, HWVersion.PCIE, application_version="2.5.4")
    with pytest.raises(NanoKVMNotSupportedError):
        await client.set_screen(ScreenSettingType.RESOLUTION, 1080)
    await client.set_screen(ScreenSettingType.GOP, 60)

    mark_detected(client, HWVersion.PCIE, application_version="2.5.2")
    await client.set_screen(ScreenSettingType.RESOLUTION, 1080)

    calls = mocked.requests[("POST", yarl.URL(url))]
    assert [call.kwargs["json"] for call in calls] == [
        {"type": "gop", "value": 60},
        {"type": "resolution", "value": 1080},
    ]
