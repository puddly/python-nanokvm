"""Tests for image and script storage operations."""

from pathlib import Path

import aiohttp
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMApiError, NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import ApiResponseCode, DownloadStatus, HWVersion, StatusImageRsp

from .common import BASE_URL, mark_detected


@pytest.mark.parametrize(
    ("hardware", "expected_prefix"),
    [
        (HWVersion.PRO, "/storage/download"),
        (HWVersion.PCIE, "/download"),
        (HWVersion.UNKNOWN, "/download"),
        (None, "/download"),
    ],
)
async def test_image_download_prefix_by_hardware_family(
    hardware: HWVersion | None,
    expected_prefix: str,
    client: NanoKVMClient,
) -> None:
    """Image download routes use the Pro prefix only for Pro hardware."""
    client._session._hw_version = hardware

    assert client._image_download_prefix() == expected_prefix


@pytest.mark.parametrize(
    "status",
    ["success", "failed", "checksum_failed"],
)
def test_download_status_accepts_terminal_states(status: str) -> None:
    """Download status responses include terminal success and failure states."""
    response = StatusImageRsp.model_validate(
        {"status": status, "file": "image.iso", "percentage": ""}
    )

    assert response.status is DownloadStatus(status)


async def test_get_images_success(client: NanoKVMClient, mocked: aioresponses) -> None:
    """Test get_images with a successful response."""
    mocked.get(
        f"{BASE_URL}storage/image",
        payload={
            "code": 0,
            "msg": "success",
            "data": {
                "files": [
                    "/data/alpine-standard-3.23.2-x86_64.iso",
                    "/data/cs10-js.iso",
                ]
            },
        },
    )

    response = await client.get_images()

    assert response is not None
    assert len(response.files) == 2
    assert "/data/alpine-standard-3.23.2-x86_64.iso" in response.files
    assert "/data/cs10-js.iso" in response.files


async def test_get_images_empty(client: NanoKVMClient, mocked: aioresponses) -> None:
    """Test get_images with an empty list."""
    mocked.get(
        f"{BASE_URL}storage/image",
        payload={"code": 0, "msg": "success", "data": {"files": []}},
    )

    response = await client.get_images()

    assert response is not None
    assert len(response.files) == 0


async def test_get_images_api_error(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test get_images with an API error response."""
    mocked.get(
        f"{BASE_URL}storage/image",
        payload={"code": -1, "msg": "failed to list images", "data": None},
    )

    with pytest.raises(NanoKVMApiError) as exc_info:
        await client.get_images()

    assert exc_info.value.code == ApiResponseCode.FAILURE
    assert "failed to list images" in exc_info.value.msg


async def test_mount_image_sends_pro_read_only_flag(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test mount_image sends the Pro readOnly field."""
    mocked.post(
        f"{BASE_URL}storage/image/mount",
        payload={"code": 0, "msg": "success", "data": None},
    )

    mark_detected(client, HWVersion.PRO, "1.2.1")
    await client.mount_image("/data/test.img", read_only=True)

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}storage/image/mount"))]
    assert calls[0].kwargs.get("json") == {
        "file": "/data/test.img",
        "cdrom": False,
        "readOnly": True,
    }


async def test_upload_script_uses_multipart_form(
    tmp_path: Path, client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test script upload uses the shared multipart helper."""
    script = tmp_path / "test.sh"
    script.write_text("#!/bin/sh\ntrue\n")

    mocked.post(
        f"{BASE_URL}vm/script/upload",
        payload={
            "code": 0,
            "msg": "success",
            "data": {"file": "test.sh"},
        },
    )

    response = await client.upload_script(script)

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}vm/script/upload"))]
    assert isinstance(calls[0].kwargs.get("data"), aiohttp.FormData)
    assert response.file == "test.sh"


async def test_mount_image_read_only_requires_pro_1_2_1(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Older Pro firmware ignores readOnly, so the client refuses it first."""
    mark_detected(client, HWVersion.PRO, "1.2.0")
    with pytest.raises(NanoKVMNotSupportedError, match="1.2.1"):
        await client.mount_image("/data/test.img", read_only=True)
    assert not mocked.requests


async def test_autostart_scripts(client: NanoKVMClient, mocked: aioresponses) -> None:
    """Autostart treats a missing directory as empty and guards script names."""
    mark_detected(client, HWVersion.PCIE, "2.5.2")
    mocked.get(
        f"{BASE_URL}vm/autostart",
        payload={"code": -1, "msg": "get autostart directory fail", "data": None},
    )
    mocked.get(
        f"{BASE_URL}vm/autostart/boot.sh",
        payload={"code": 0, "msg": "success", "data": "#!/bin/sh\n"},
    )
    mocked.post(
        f"{BASE_URL}vm/autostart/boot.sh",
        payload={"code": 0, "msg": "success", "data": "boot.sh"},
    )

    assert (await client.get_autostart_scripts()).files == []
    assert await client.get_autostart_script("boot.sh") == "#!/bin/sh\n"
    await client.upload_autostart_script("boot.sh", "true\n")
    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}vm/autostart/boot.sh"))]
    assert calls[0].kwargs["json"] == {"content": "true\n"}
    for name in ("", "..", "../kvm/server.yaml"):
        with pytest.raises(ValueError):
            await client.delete_autostart_script(name)
