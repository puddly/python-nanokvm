from pathlib import Path

import aiohttp
from aiohttp import ClientSession
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMApiError, NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import (
    ApiResponseCode,
    DiskType,
    GetMacRsp,
    GetOLEDRsp,
    HWVersion,
    MouseJigglerMode,
    OledType,
    ShortcutKey,
    StreamMode,
    VirtualDevice,
)


async def test_get_images_success() -> None:
    """Test get_images with a successful response."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/storage/image",
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


async def test_get_images_empty() -> None:
    """Test get_images with an empty list."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/storage/image",
                payload={"code": 0, "msg": "success", "data": {"files": []}},
            )

            response = await client.get_images()

            assert response is not None
            assert len(response.files) == 0


async def test_get_images_api_error() -> None:
    """Test get_images with an API error response."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/storage/image",
                payload={"code": -1, "msg": "failed to list images", "data": None},
            )

            with pytest.raises(NanoKVMApiError) as exc_info:
                await client.get_images()

            assert exc_info.value.code == ApiResponseCode.FAILURE
            assert "failed to list images" in exc_info.value.msg


async def test_api_error_allows_endpoint_specific_codes() -> None:
    """Test endpoint-specific API error codes are surfaced as API errors."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/storage/image/mount",
                payload={"code": -6, "msg": "mount image failed", "data": None},
            )

            with pytest.raises(NanoKVMApiError) as exc_info:
                await client.mount_image("/data/missing.iso", read_only=True)

            assert exc_info.value.code == -6
            assert "mount image failed" in exc_info.value.msg


async def test_none_returning_endpoint_preserves_unknown_api_code() -> None:
    """Test unknown API codes from None-returning endpoints remain API errors."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/vm/web-title",
                payload={"code": -4, "msg": "failed to set title", "data": None},
            )

            with pytest.raises(NanoKVMApiError) as exc_info:
                await client.set_web_title("NanoKVM")

            assert exc_info.value.code == -4
            assert "failed to set title" in exc_info.value.msg


async def test_mount_image_sends_pro_read_only_flag() -> None:
    """Test mount_image sends the Pro readOnly field."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/storage/image/mount",
                payload={"code": 0, "msg": "success", "data": None},
            )

            await client.mount_image("/data/test.img", read_only=True)

            calls = m.requests[
                ("POST", yarl.URL("http://localhost:8888/api/storage/image/mount"))
            ]
            assert calls[0].kwargs.get("json") == {
                "file": "/data/test.img",
                "cdrom": False,
                "readOnly": True,
            }


async def test_get_oled_info_raises_for_pro_invalid_file_content() -> None:
    """Test Pro OLED invalid file content follows upstream error semantics."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        client._hw_version = HWVersion.PRO

        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/vm/oled",
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
            info_url = yarl.URL("http://localhost:8888/api/vm/info")
            assert ("GET", info_url) not in m.requests


async def test_set_mouse_jiggler_state_noops_when_already_disabled() -> None:
    """Test disabling mouse jiggler is idempotent."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/vm/mouse-jiggler",
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

            post_url = yarl.URL("http://localhost:8888/api/vm/mouse-jiggler/")
            assert ("POST", post_url) not in m.requests


async def test_update_virtual_device_ignores_success_data() -> None:
    """Test update_virtual_device handles non-Pro success payloads."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/vm/device/virtual",
                payload={"code": 0, "msg": "success", "data": {"on": True}},
            )

            await client.update_virtual_device(VirtualDevice.DISK)

            calls = m.requests[
                ("POST", yarl.URL("http://localhost:8888/api/vm/device/virtual"))
            ]
            assert calls[0].kwargs.get("json") == {"device": "disk"}


async def test_get_virtual_device_status_handles_non_pro_shape() -> None:
    """Test virtual device status keeps non-Pro fields intact."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/vm/device/virtual",
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


async def test_get_virtual_device_status_handles_pro_shape() -> None:
    """Test Pro virtual device status exposes Pro fields without fake non-Pro disk."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        client._hw_version = HWVersion.PRO

        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/vm/device/virtual",
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


async def test_update_virtual_device_sends_pro_disk_type() -> None:
    """Test Pro disk virtual device requests include the disk type."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        client._hw_version = HWVersion.PRO

        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/vm/device/virtual",
                payload={"code": 0, "msg": "success", "data": None},
            )

            await client.update_virtual_device(
                VirtualDevice.DISK,
                disk_type=DiskType.EMMC,
            )

            calls = m.requests[
                ("POST", yarl.URL("http://localhost:8888/api/vm/device/virtual"))
            ]
            assert calls[0].kwargs.get("json") == {
                "device": "disk",
                "type": "emmc",
            }


async def test_set_led_strip_partial_preserves_current_config() -> None:
    """Test partial LED updates post a complete Pro LED configuration."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        client._hw_version = HWVersion.PRO

        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/vm/ledstrip/get",
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
            m.post(
                "http://localhost:8888/api/vm/ledstrip/set",
                payload={"code": 0, "msg": "success", "data": None},
            )

            await client.set_led_strip(brightness=50)

            calls = m.requests[
                ("POST", yarl.URL("http://localhost:8888/api/vm/ledstrip/set"))
            ]
            assert calls[0].kwargs.get("json") == {
                "on": True,
                "hor": 8,
                "ver": 6,
                "brightness": 50,
            }


async def test_get_wol_macs_returns_empty_list_for_missing_file() -> None:
    """Test WOL getter normalizes the empty-file device state."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/network/wol/mac",
                payload={"code": -2, "msg": "open file error", "data": None},
            )

            response = await client.get_wol_macs()

            assert response.macs == []


def test_model_normalizations_for_wol_and_oled() -> None:
    """Test model-only compatibility normalizers."""
    assert GetMacRsp(macs=["", "AA:BB:CC:DD:EE:FF", "   "]).macs == [
        "AA:BB:CC:DD:EE:FF"
    ]
    assert GetOLEDRsp(exist=True, type="desk", sleep=0).type == OledType.DESK


async def test_connect_wifi_no_auth_sends_ap_header() -> None:
    """Test AP-mode Wi-Fi connection sends setup credentials without auth cookie."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/network/wifi",
                payload={"code": 0, "msg": "success", "data": None},
            )

            await client.connect_wifi_no_auth(
                "NanoKVM-Setup",
                ap_password="setup-secret",
            )

            calls = m.requests[
                ("POST", yarl.URL("http://localhost:8888/api/network/wifi"))
            ]
            assert calls[0].kwargs.get("json") == {
                "ssid": "NanoKVM-Setup",
                "password": "",
            }
            assert calls[0].kwargs.get("cookies") == {}
            assert calls[0].kwargs.get("headers", {})["X-AP-Key"] == "setup-secret"


async def test_upload_script_uses_multipart_form(tmp_path: Path) -> None:
    """Test script upload uses the shared multipart helper."""
    script = tmp_path / "test.sh"
    script.write_text("#!/bin/sh\ntrue\n")

    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/vm/script/upload",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {"file": "test.sh"},
                },
            )

            response = await client.upload_script(script)

            calls = m.requests[
                ("POST", yarl.URL("http://localhost:8888/api/vm/script/upload"))
            ]
            assert isinstance(calls[0].kwargs.get("data"), aiohttp.FormData)
            assert response.file == "test.sh"


async def test_shortcut_methods_send_expected_payloads() -> None:
    """Test custom shortcut client helpers use the expected endpoints."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/hid/shortcut",
                payload={"code": 0, "msg": "success", "data": None},
            )
            m.delete(
                "http://localhost:8888/api/hid/shortcut",
                payload={"code": 0, "msg": "success", "data": None},
            )
            m.post(
                "http://localhost:8888/api/hid/shortcut/leader-key",
                payload={"code": 0, "msg": "success", "data": None},
            )

            key = ShortcutKey(code="ControlLeft", label="Ctrl")
            await client.add_shortcut([key])
            await client.delete_shortcut("shortcut-1")
            await client.set_leader_key("ControlLeft")

            shortcut_calls = m.requests[
                ("POST", yarl.URL("http://localhost:8888/api/hid/shortcut"))
            ]
            delete_calls = m.requests[
                ("DELETE", yarl.URL("http://localhost:8888/api/hid/shortcut"))
            ]
            leader_calls = m.requests[
                (
                    "POST",
                    yarl.URL("http://localhost:8888/api/hid/shortcut/leader-key"),
                )
            ]
            assert shortcut_calls[0].kwargs.get("json") == {
                "keys": [{"code": "ControlLeft", "label": "Ctrl"}]
            }
            assert delete_calls[0].kwargs.get("json") == {"id": "shortcut-1"}
            assert leader_calls[0].kwargs.get("json") == {"key": "ControlLeft"}


async def test_tailscale_login_returns_url() -> None:
    """Test Tailscale login returns the upstream login URL payload."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/extensions/tailscale/login",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {"url": "https://login.tailscale.com/a/test"},
                },
            )

            response = await client.tailscale_login()

            assert response.url == "https://login.tailscale.com/a/test"


async def test_set_stream_mode_accepts_existing_string_values() -> None:
    """Test set_stream_mode remains source-compatible with valid strings."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        client._hw_version = HWVersion.PRO

        with aioresponses() as m:
            m.post(
                "http://localhost:8888/api/stream/mode",
                payload={"code": 0, "msg": "success", "data": None},
            )

            await client.set_stream_mode(StreamMode.H264_DIRECT.value)

            calls = m.requests[
                ("POST", yarl.URL("http://localhost:8888/api/stream/mode"))
            ]
            assert calls[0].kwargs.get("json") == {"mode": "h264-direct"}


async def test_enable_swap_404_is_not_supported() -> None:
    """Test non-Pro swap enable 404 becomes NanoKVMNotSupportedError."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        client._hw_version = HWVersion.PCIE

        with aioresponses() as m:
            m.post("http://localhost:8888/api/vm/swap/enable", status=404)

            with pytest.raises(NanoKVMNotSupportedError) as exc_info:
                await client.enable_swap()

            assert "enable_swap is unavailable on this non-Pro" in str(exc_info.value)


async def test_disable_swap_404_is_not_supported() -> None:
    """Test non-Pro swap disable 404 becomes NanoKVMNotSupportedError."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        client._hw_version = HWVersion.PCIE

        with aioresponses() as m:
            m.post("http://localhost:8888/api/vm/swap/disable", status=404)

            with pytest.raises(NanoKVMNotSupportedError) as exc_info:
                await client.disable_swap()

            assert "disable_swap is unavailable on this non-Pro" in str(exc_info.value)


async def test_client_context_manager() -> None:
    """Test that client properly initializes and cleans up with context manager."""
    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        # Verify session is created
        assert client._session is not None
        assert not client._session.closed

    # After exiting context, session should be closed
    assert client._session is None


async def test_client_context_manager_external_session() -> None:
    """Test that client properly deals with an external session."""
    async with ClientSession() as session:
        client3 = NanoKVMClient("http://localhost:8888/api/", session=session)

        # All clients connect with the same external session
        async with (
            NanoKVMClient("http://localhost:8888/api/", session=session) as client1,
            NanoKVMClient("http://localhost:8888/api/", session=session) as client2,
            client3,
        ):
            # Verify session is created
            assert client1._session is session
            assert client2._session is session
            assert client3._session is session

        # Reusing a client with an external session should not close the session
        async with client3:
            assert client3._session is session

        assert not session.closed

    assert session.closed
