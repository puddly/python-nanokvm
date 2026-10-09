"""Fixtures shared by the test modules."""

from collections.abc import AsyncIterator, Iterator

from aioresponses import aioresponses
import pytest

from nanokvm.client import NanoKVMClient

from .common import BASE_URL


@pytest.fixture
async def client() -> AsyncIterator[NanoKVMClient]:
    """A started client for ``BASE_URL`` that already holds a session token."""
    async with NanoKVMClient(BASE_URL, token="test-token") as client:
        yield client


@pytest.fixture
def mocked() -> Iterator[aioresponses]:
    """Mock every aiohttp request made during the test."""
    with aioresponses() as mocked:
        yield mocked
