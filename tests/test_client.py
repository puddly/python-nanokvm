"""Tests for the client facade and the shared response envelope."""

from aiohttp import ClientSession
from aioresponses import aioresponses
import pytest

from nanokvm.client import NanoKVMApiError, NanoKVMClient, NanoKVMInvalidResponseError
from nanokvm.models import GetMacRsp, GetOLEDRsp, HWVersion, OledType

from .common import BASE_URL, info_payload, mark_detected


async def test_typed_success_response_requires_data(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A typed endpoint rejects a successful response without its payload."""
    mocked.get(
        f"{BASE_URL}storage/image",
        payload={"code": 0, "msg": "success", "data": None},
    )

    with pytest.raises(NanoKVMInvalidResponseError, match="missing data"):
        await client.get_images()


async def test_api_error_data_is_not_validated_as_success_model(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """An error envelope is classified before its endpoint data is parsed."""
    mocked.get(
        f"{BASE_URL}storage/image",
        payload={
            "code": -1,
            "msg": "failed to list images",
            "data": {"synthetic": "unexpected"},
        },
    )

    with pytest.raises(NanoKVMApiError) as exc_info:
        await client.get_images()

    assert exc_info.value.code == -1


async def test_detect_versions_stores_application_and_image(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test detect_versions caches versions from /vm/info."""
    mocked.get(
        f"{BASE_URL}vm/info",
        payload=info_payload(application="2.4.1", image="1.4.0"),
    )

    await client.detect_versions()

    assert client.application_version == "2.4.1"
    assert client.image_version == "1.4.0"


async def test_api_error_allows_endpoint_specific_codes(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test endpoint-specific API error codes are surfaced as API errors."""
    mocked.post(
        f"{BASE_URL}storage/image/mount",
        payload={"code": -6, "msg": "mount image failed", "data": None},
    )

    mark_detected(client, HWVersion.PRO, "1.2.15")
    with pytest.raises(NanoKVMApiError) as exc_info:
        await client.mount_image("/data/missing.iso", read_only=True)

    assert exc_info.value.code == -6
    assert "mount image failed" in exc_info.value.msg


async def test_none_returning_endpoint_preserves_unknown_api_code(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test unknown API codes from None-returning endpoints remain API errors."""
    mark_detected(client)

    mocked.post(
        f"{BASE_URL}vm/web-title",
        payload={"code": -4, "msg": "failed to set title", "data": None},
    )

    with pytest.raises(NanoKVMApiError) as exc_info:
        await client.set_web_title("NanoKVM")

    assert exc_info.value.code == -4
    assert "failed to set title" in exc_info.value.msg


def test_model_normalizations_for_wol_and_oled() -> None:
    """Test model-only compatibility normalizers."""
    assert GetMacRsp(macs=["", "AA:BB:CC:DD:EE:FF", "   "]).macs == [
        "AA:BB:CC:DD:EE:FF"
    ]
    assert (
        GetOLEDRsp.model_validate({"exist": True, "type": "desk", "sleep": 0}).type
        == OledType.DESK
    )


async def test_client_context_manager() -> None:
    """Test that client properly initializes and cleans up with context manager."""
    async with NanoKVMClient(BASE_URL, token="test-token") as client:
        # Verify session is created
        assert client._session._http_session is not None
        assert not client._session._http_session.closed

    # After exiting context, session should be closed
    assert client._session._http_session is None


async def test_client_context_manager_external_session() -> None:
    """Test that client properly deals with an external session."""
    async with ClientSession() as session:
        client3 = NanoKVMClient(BASE_URL, session=session)

        # All clients connect with the same external session
        async with (
            NanoKVMClient(BASE_URL, session=session) as client1,
            NanoKVMClient(BASE_URL, session=session) as client2,
            client3,
        ):
            # Verify session is created
            assert client1._session._http_session is session
            assert client2._session._http_session is session
            assert client3._session._http_session is session

        # Reusing a client with an external session should not close the session
        async with client3:
            assert client3._session._http_session is session

        assert not session.closed

    assert session.closed


async def test_request_outside_context_manager_explains_how_to_start() -> None:
    """Using the client without 'async with' raises a clear error."""
    client = NanoKVMClient(BASE_URL, token="synthetic-token")
    with pytest.raises(RuntimeError, match="async with NanoKVMClient"):
        await client.get_gpio()
