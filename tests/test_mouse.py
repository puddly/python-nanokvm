"""Tests for mouse control functionality."""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nanokvm.client import NanoKVMClient
from nanokvm.models import MouseButton


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
            yield client, mock_ws


def _sent_reports(mock_ws: AsyncMock) -> list[bytes]:
    return [call.args[0] for call in mock_ws.send_bytes.call_args_list]


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
