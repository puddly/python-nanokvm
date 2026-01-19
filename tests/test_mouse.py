"""Tests for mouse control functionality."""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nanokvm.client import NanoKVMClient
from nanokvm.models import MouseButton


@pytest.fixture
async def client_with_mock_ws() -> AsyncGenerator[tuple[NanoKVMClient, AsyncMock], Any]:
    """Fixture that provides a client with mocked WebSocket."""
    mock_ws = AsyncMock()
    mock_ws.closed = False

    with patch(
        "aiohttp.ClientSession.ws_connect", new_callable=AsyncMock, return_value=mock_ws
    ):
        async with NanoKVMClient(
            "http://localhost:8888/api/", token="test-token"
        ) as client:
            yield client, mock_ws


async def test_mouse_move_abs(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Test absolute mouse movement."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_move_abs(0.5, 0.5)

    # Verify the WebSocket send was called with correct parameters
    mock_ws.send_json.assert_called_once()
    message = mock_ws.send_json.call_args[0][0]
    # Message format: [2, event_type, button_state, x_val, y_val]
    assert message[0] == 2  # mouse event indicator
    assert message[1] == 2  # move_abs
    assert message[2] == 0  # button state
    # 0.5 * 32768 = 16384
    assert message[3] == 16384
    assert message[4] == 16384


async def test_mouse_move_rel(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Test relative mouse movement."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_move_rel(0.1, -0.1)

    # Verify the WebSocket send was called with correct parameters
    mock_ws.send_json.assert_called_once()
    message = mock_ws.send_json.call_args[0][0]
    # Message format: [2, event_type, button_state, x_val, y_val]
    assert message[0] == 2  # mouse event indicator
    assert message[1] == 3  # move_rel
    assert message[2] == 0  # button state
    # 0.1 * 32768 = 3276.8 -> 3276
    assert message[3] == 3276
    # -0.1 * 32768 = -3276.8 -> -3276
    assert message[4] == -3276


async def test_mouse_down(client_with_mock_ws: tuple[NanoKVMClient, AsyncMock]) -> None:
    """Test mouse button down."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_down(MouseButton.LEFT)

    # Verify the WebSocket send was called with correct parameters
    mock_ws.send_json.assert_called_once()
    message = mock_ws.send_json.call_args[0][0]
    # Message format: [2, event_type, button_state, x_val, y_val]
    assert message[0] == 2  # mouse event indicator
    assert message[1] == 1  # mouse_down
    assert message[2] == 1  # left button
    assert message[3] == 0
    assert message[4] == 0


async def test_mouse_up(client_with_mock_ws: tuple[NanoKVMClient, AsyncMock]) -> None:
    """Test mouse button up."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_up()

    # Verify the WebSocket send was called with correct parameters
    mock_ws.send_json.assert_called_once()
    message = mock_ws.send_json.call_args[0][0]
    # Message format: [2, event_type, button_state, x_val, y_val]
    assert message[0] == 2  # mouse event indicator
    assert message[1] == 0  # mouse_up
    assert message[2] == 0  # button state (always 0 for mouse_up)
    assert message[3] == 0
    assert message[4] == 0


async def test_mouse_click_without_position(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Test mouse click at current position."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_click(MouseButton.LEFT)

    # Should send two events: down and up
    assert mock_ws.send_json.call_count == 2

    # First call should be mouse_down
    first_message = mock_ws.send_json.call_args_list[0][0][0]
    assert first_message[0] == 2  # mouse event indicator
    assert first_message[1] == 1  # mouse_down
    assert first_message[2] == 1  # left button

    # Second call should be mouse_up
    second_message = mock_ws.send_json.call_args_list[1][0][0]
    assert second_message[0] == 2  # mouse event indicator
    assert second_message[1] == 0  # mouse_up
    assert second_message[2] == 0  # button state (always 0 for mouse_up)


async def test_mouse_click_with_position(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Test mouse click at specific position."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_click(MouseButton.MIDDLE, 0.25, 0.75)

    # Should send three events: move_abs, down, and up
    assert mock_ws.send_json.call_count == 3

    # First call should be move_abs
    first_message = mock_ws.send_json.call_args_list[0][0][0]
    assert first_message[0] == 2  # mouse event indicator
    assert first_message[1] == 2  # move_abs
    assert first_message[3] == int(0.25 * 32768)
    assert first_message[4] == int(0.75 * 32768)

    # Second call should be mouse_down
    second_message = mock_ws.send_json.call_args_list[1][0][0]
    assert second_message[0] == 2  # mouse event indicator
    assert second_message[1] == 1  # mouse_down
    assert second_message[2] == 4  # middle button

    # Third call should be mouse_up
    third_message = mock_ws.send_json.call_args_list[2][0][0]
    assert third_message[0] == 2  # mouse event indicator
    assert third_message[1] == 0  # mouse_up
    assert third_message[2] == 0  # button state (always 0 for mouse_up)


async def test_mouse_scroll(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Test mouse scroll."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_scroll(0.1, -0.2)

    # Verify the WebSocket send was called with correct parameters
    mock_ws.send_json.assert_called_once()
    message = mock_ws.send_json.call_args[0][0]
    # Message format: [2, event_type, button_state, x_val, y_val]
    assert message[0] == 2  # mouse event indicator
    assert message[1] == 4  # scroll
    assert message[2] == 0  # button state
    # 0.1 * 32768 = 3276.8 -> 3276
    assert message[3] == 3276
    # -0.2 * 32768 = -6553.6 -> -6553
    assert message[4] == -6553


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
            # Trigger WebSocket creation by making a call
            await client.mouse_move_abs(0.0, 0.0)
            # Verify WebSocket was created
            assert client._ws is not None
            assert client._session is not None

        # After exiting context, resources should be cleaned up
        mock_ws.close.assert_called_once()
        assert client._ws is None
        assert client._session is None


async def test_button_mapping(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Test different button types."""
    client, mock_ws = client_with_mock_ws

    # Test left button
    await client.mouse_down(MouseButton.LEFT)
    message = mock_ws.send_json.call_args[0][0]
    assert message[2] == MouseButton.LEFT

    # Test right button
    await client.mouse_down(MouseButton.RIGHT)
    message = mock_ws.send_json.call_args[0][0]
    assert message[2] == MouseButton.RIGHT

    # Test middle button
    await client.mouse_down(MouseButton.MIDDLE)
    message = mock_ws.send_json.call_args[0][0]
    assert message[2] == MouseButton.MIDDLE
