"""Tests for mouse control functionality."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nanokvm.client import NanoKVMClient, NanoKVMNotAuthenticatedError
from nanokvm.models import HWVersion, MouseButton


@pytest.fixture
async def client_with_mock_ws() -> AsyncGenerator[tuple[NanoKVMClient, AsyncMock], Any]:
    """Fixture that provides a client with a mocked WebSocket."""
    mock_ws = AsyncMock()
    mock_ws.closed = False

    with patch(
        "aiohttp.ClientSession.ws_connect", new_callable=AsyncMock, return_value=mock_ws
    ):
        async with NanoKVMClient(
            "http://localhost:8888/api/", token="test-token"
        ) as client:
            client._hw_version = HWVersion.PCIE
            client._application_version = "2.3.2"
            yield client, mock_ws


def _sent_reports(mock_ws: AsyncMock) -> list[bytes]:
    return [call.args[0] for call in mock_ws.send_bytes.call_args_list]


@pytest.fixture
async def legacy_client_with_mock_ws() -> AsyncGenerator[
    tuple[NanoKVMClient, AsyncMock], Any
]:
    """Fixture that provides a pre-2.3.2 client and mocked WebSocket."""
    mock_ws = AsyncMock()
    mock_ws.closed = False

    with patch(
        "aiohttp.ClientSession.ws_connect", new_callable=AsyncMock, return_value=mock_ws
    ):
        async with NanoKVMClient(
            "http://localhost:8888/api/", token="test-token"
        ) as client:
            client._hw_version = HWVersion.PCIE
            client._application_version = "2.3.1"
            yield client, mock_ws


def _sent_events(mock_ws: AsyncMock) -> list[list[int]]:
    return [call.args[0] for call in mock_ws.send_json.call_args_list]


async def test_mouse_move_abs_sends_hid_report(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Absolute movement uses the binary six-byte HID report."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_move_abs(0.5, 0.5)

    assert _sent_reports(mock_ws) == [bytes([2, 0, 0, 64, 0, 64, 0])]
    mock_ws.send_json.assert_not_called()


async def test_mouse_move_rel_sends_signed_hid_report(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Relative movement is clamped and encoded as signed HID bytes."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_move_rel(0.1, -0.1)

    assert _sent_reports(mock_ws) == [bytes([2, 0, 13, 243, 0])]


async def test_mouse_buttons_preserve_hid_state(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Button reports use a bitmask and release all buttons on mouse_up."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_down(MouseButton.LEFT)
    await client.mouse_down(MouseButton.RIGHT)
    await client.mouse_up()

    assert _sent_reports(mock_ws) == [
        bytes([2, 1, 0, 0, 0]),
        bytes([2, 3, 0, 0, 0]),
        bytes([2, 0, 0, 0, 0]),
    ]


async def test_mouse_click_with_absolute_position(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """An absolute click preserves its position in button reports."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_click(MouseButton.MIDDLE, 0.25, 0.75)

    assert _sent_reports(mock_ws) == [
        bytes([2, 0, 0, 32, 255, 95, 0]),
        bytes([2, 4, 0, 32, 255, 95, 0]),
        bytes([2, 0, 0, 32, 255, 95, 0]),
    ]


async def test_mouse_scroll_encodes_vertical_wheel(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Scrolling uses the wheel byte of the relative HID report."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_scroll(0.1, -0.2)

    assert _sent_reports(mock_ws) == [bytes([2, 0, 0, 0, 231])]


async def test_legacy_mouse_move_abs_sends_json_event(
    legacy_client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Non-Pro versions through 2.3.1 use the original JSON protocol."""
    client, mock_ws = legacy_client_with_mock_ws

    await client.mouse_move_abs(0.5, 0.5)

    assert _sent_events(mock_ws) == [[2, 2, 0, 16384, 16384]]
    mock_ws.send_bytes.assert_not_called()


async def test_legacy_mouse_move_and_scroll_preserve_json_values(
    legacy_client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Legacy relative movement and scrolling retain their integer payloads."""
    client, mock_ws = legacy_client_with_mock_ws

    await client.mouse_move_rel(0.1, -0.1)
    await client.mouse_scroll(0.1, -0.2)

    assert _sent_events(mock_ws) == [
        [2, 3, 0, 3276, -3276],
        [2, 4, 0, 3276, -6553],
    ]


async def test_legacy_mouse_buttons_use_original_event_shape(
    legacy_client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Legacy button events carry one button value rather than a HID bitmask."""
    client, mock_ws = legacy_client_with_mock_ws

    await client.mouse_down(MouseButton.LEFT)
    await client.mouse_down(MouseButton.RIGHT)
    await client.mouse_up()

    assert _sent_events(mock_ws) == [
        [2, 1, 1, 0, 0],
        [2, 1, 2, 0, 0],
        [2, 0, 0, 0, 0],
    ]


async def test_concurrent_first_mouse_use_creates_one_websocket() -> None:
    """Concurrent first use shares one lazily-created WebSocket."""
    mock_ws = AsyncMock()
    mock_ws.closed = False
    connect_started = asyncio.Event()
    allow_connect = asyncio.Event()
    connect_count = 0

    async def connect(*_args: Any, **_kwargs: Any) -> AsyncMock:
        nonlocal connect_count
        connect_count += 1
        connect_started.set()
        await allow_connect.wait()
        return mock_ws

    with patch("aiohttp.ClientSession.ws_connect", new=connect):
        async with NanoKVMClient(
            "http://localhost:8888/api/", token="test-token"
        ) as client:
            client._hw_version = HWVersion.PCIE
            client._application_version = "2.3.2"
            first = asyncio.create_task(client.mouse_move_rel(0.1, 0.0))
            await connect_started.wait()
            second = asyncio.create_task(client.mouse_move_rel(0.0, 0.1))
            allow_connect.set()
            await asyncio.gather(first, second)

    assert connect_count == 1
    assert mock_ws.send_bytes.await_count == 2


async def test_mouse_send_reconnects_after_transport_failure() -> None:
    """A failed send invalidates the cached WebSocket for the next operation."""
    first_ws = AsyncMock()
    first_ws.closed = False
    first_ws.send_bytes.side_effect = ConnectionResetError()
    second_ws = AsyncMock()
    second_ws.closed = False
    connect = AsyncMock(side_effect=[first_ws, second_ws])

    with patch("aiohttp.ClientSession.ws_connect", new=connect):
        async with NanoKVMClient(
            "http://localhost:8888/api/", token="test-token"
        ) as client:
            client._hw_version = HWVersion.PCIE
            client._application_version = "2.3.2"
            with pytest.raises(ConnectionResetError):
                await client.mouse_move_rel(0.1, 0.0)
            await client.mouse_move_rel(0.1, 0.0)

    assert connect.await_count == 2
    first_ws.close.assert_awaited_once()


async def test_logout_closes_websocket_and_prevents_further_mouse_input(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Logout invalidates the transport and local pressed-button state."""
    client, mock_ws = client_with_mock_ws
    await client.mouse_move_rel(0.1, 0.0)
    client._mouse_buttons = int(MouseButton.LEFT)

    with patch.object(client, "_api_request_json", new_callable=AsyncMock):
        await client.logout()

    assert client.token is None
    assert client._mouse_buttons == 0
    mock_ws.close.assert_awaited_once()
    assert client._ws is None
    with pytest.raises(NanoKVMNotAuthenticatedError, match="not authenticated"):
        await client.mouse_move_rel(0.1, 0.0)


async def test_pro_mouse_uses_binary_protocol(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Pro application versions from 1.2.6 use binary HID reports."""
    client, mock_ws = client_with_mock_ws
    client._hw_version = HWVersion.PRO
    client._application_version = "1.2.6"

    await client.mouse_move_abs(0.5, 0.5)

    assert _sent_reports(mock_ws) == [bytes([2, 0, 0, 64, 0, 64, 0])]


async def test_legacy_pro_mouse_uses_json_protocol(
    legacy_client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Pro application versions before 1.2.6 use the legacy JSON protocol."""
    client, mock_ws = legacy_client_with_mock_ws
    client._hw_version = HWVersion.PRO
    client._application_version = "1.2.5"

    await client.mouse_move_abs(0.5, 0.5)

    assert _sent_events(mock_ws) == [[2, 2, 0, 16384, 16384]]
    mock_ws.send_bytes.assert_not_called()


async def test_context_manager_cleanup() -> None:
    """Test that context manager properly closes WebSocket and session."""
    mock_ws = AsyncMock()
    mock_ws.closed = False

    with patch(
        "aiohttp.ClientSession.ws_connect", new_callable=AsyncMock, return_value=mock_ws
    ):
        async with NanoKVMClient(
            "http://localhost:8888/api/", token="test-token"
        ) as client:
            client._hw_version = HWVersion.PCIE
            client._application_version = "2.3.2"
            await client.mouse_move_abs(0.0, 0.0)
            assert client._ws is not None
            assert client._session is not None

        mock_ws.close.assert_called_once()
        assert client._ws is None
        assert client._session is None


async def test_button_mapping(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Test different button types."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_down(MouseButton.LEFT)
    await client.mouse_up()
    await client.mouse_down(MouseButton.RIGHT)
    await client.mouse_up()
    await client.mouse_down(MouseButton.MIDDLE)

    reports = _sent_reports(mock_ws)
    assert reports[0][1] == MouseButton.LEFT
    assert reports[2][1] == MouseButton.RIGHT
    assert reports[4][1] == MouseButton.MIDDLE
