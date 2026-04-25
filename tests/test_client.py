from aiohttp import ClientSession
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMApiError, NanoKVMClient
from nanokvm.models import (
    ApiResponseCode,
    HWVersion,
    MouseJigglerMode,
    OledType,
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

            assert exc_info.value.code == ApiResponseCode.ENDPOINT_ERROR_6
            assert "mount image failed" in exc_info.value.msg


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


async def test_get_oled_info_falls_back_for_pro_invalid_file_content() -> None:
    """Test Pro OLED fallback for firmware that rejects lower-case desk IDs."""
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
            m.get(
                "http://localhost:8888/api/vm/info",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "ips": [],
                        "mdns": "kvm-test.local",
                        "image": "v1.0.14",
                        "application": "1.2.14",
                        "deviceKey": "test-device",
                        "pn": "unknown",
                        "arch": "aarch64",
                    },
                },
            )

            response = await client.get_oled_info()

            assert response.exist is True
            assert response.type == OledType.DESK
            assert response.sleep == 0


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
