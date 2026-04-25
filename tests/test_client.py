from aiohttp import ClientSession
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMApiError, NanoKVMClient
from nanokvm.models import (
    ApiResponseCode,
    HWVersion,
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
