from aiohttp import ClientSession
from aioresponses import aioresponses

from nanokvm.client import NanoKVMApiError, NanoKVMClient
from nanokvm.models import ApiResponseCode


async def test_client() -> None:
    """Test the NanoKVMClient."""
    async with ClientSession() as session:
        client = NanoKVMClient("http://localhost:8888/api/", session)
        assert client is not None


async def test_get_images_success() -> None:
    """Test get_images with a successful response."""
    async with ClientSession() as session:
        client = NanoKVMClient(
            "http://localhost:8888/api/", session, token="test-token"
        )

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
    async with ClientSession() as session:
        client = NanoKVMClient(
            "http://localhost:8888/api/", session, token="test-token"
        )

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
    async with ClientSession() as session:
        client = NanoKVMClient(
            "http://localhost:8888/api/", session, token="test-token"
        )

        with aioresponses() as m:
            m.get(
                "http://localhost:8888/api/storage/image",
                payload={"code": -1, "msg": "failed to list images", "data": None},
            )

            try:
                await client.get_images()
                raise AssertionError("Expected NanoKVMApiError to be raised")
            except NanoKVMApiError as e:
                assert e.code == ApiResponseCode.FAILURE
                assert "failed to list images" in e.msg
