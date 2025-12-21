from aioresponses import aioresponses
import pytest

from nanokvm.client import NanoKVMApiError, NanoKVMClient
from nanokvm.models import ApiResponseCode


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
