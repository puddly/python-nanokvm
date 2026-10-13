"""Tests for device services."""

from aioresponses import aioresponses

from nanokvm.client import NanoKVMClient

from .common import BASE_URL, mark_detected


async def test_tailscale_login_returns_url(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test Tailscale login returns the upstream login URL payload."""
    mark_detected(client, application_version="2.1.6")

    mocked.post(
        f"{BASE_URL}extensions/tailscale/login",
        payload={
            "code": 0,
            "msg": "success",
            "data": {"url": "https://login.tailscale.com/a/test"},
        },
    )

    response = await client.tailscale_login()

    assert response.url == "https://login.tailscale.com/a/test"
