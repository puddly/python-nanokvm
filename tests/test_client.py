from aiohttp import ClientSession

from nanokvm.client import NanoKVMClient


async def test_client() -> None:
    """Test the NanoKVMClient."""
    async with ClientSession() as session:
        client = NanoKVMClient("http://localhost:8888/api/", session)
        assert client is not None
