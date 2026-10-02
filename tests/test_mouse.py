"""Tests for mouse control functionality."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, Literal
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from nanokvm.client import (
    NanoKVMClient,
    NanoKVMNotAuthenticatedError,
    NanoKVMNotSupportedError,
)
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
            client._session._hw_version = HWVersion.PCIE
            client._session._application_version = "2.3.2"
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
            client._session._hw_version = HWVersion.PCIE
            client._session._application_version = "2.3.1"
            yield client, mock_ws


def _sent_events(mock_ws: AsyncMock) -> list[list[int]]:
    return [call.args[0] for call in mock_ws.send_json.call_args_list]


@pytest.mark.parametrize(
    ("mode", "wheel", "expected"),
    [
        ("absolute", -2, bytes((3, 0x34, 0x12, 0x78, 0x56, 0xFE))),
        ("relative", -2, bytes((3, 0, 0, 0xFE))),
    ],
)
def test_current_mouse_report_uses_selected_mode(
    mode: Literal["absolute", "relative"], wheel: int, expected: bytes
) -> None:
    """Current button and wheel reports follow the selected mouse mode."""
    client = NanoKVMClient("http://localhost:8888/api/", token="test-token")
    client._session._mouse_mode = mode
    client._session._mouse_buttons = 3
    client._session._mouse_abs_position = (0x1234, 0x5678)

    assert client._mouse.report_for_current_mode(wheel=wheel) == expected


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


@pytest.mark.parametrize(
    ("button", "value"),
    [(MouseButton.BACK, 8), (MouseButton.FORWARD, 16)],
)
def test_mouse_button_values_include_browser_navigation_buttons(
    button: MouseButton, value: int
) -> None:
    """Back and Forward use the HID button bits introduced in 2.3.2."""
    assert button.value == value


@pytest.mark.parametrize("button", [MouseButton.BACK, MouseButton.FORWARD])
async def test_mouse_navigation_buttons_use_binary_hid_reports(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock], button: MouseButton
) -> None:
    """Binary mouse reports expose Back and Forward button bits."""
    client, mock_ws = client_with_mock_ws

    await client.mouse_down(button)
    await client.mouse_up()

    assert _sent_reports(mock_ws) == [
        bytes([2, button.value, 0, 0, 0]),
        bytes([2, 0, 0, 0, 0]),
    ]


@pytest.mark.parametrize("button", [MouseButton.BACK, MouseButton.FORWARD])
async def test_mouse_navigation_buttons_are_rejected_by_legacy_protocol(
    legacy_client_with_mock_ws: tuple[NanoKVMClient, AsyncMock], button: MouseButton
) -> None:
    """Legacy JSON mouse protocol cannot represent Back or Forward."""
    client, mock_ws = legacy_client_with_mock_ws

    with pytest.raises(NanoKVMNotSupportedError, match="binary mouse protocol"):
        await client.mouse_down(button)

    mock_ws.send_json.assert_not_called()


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


async def test_legacy_mouse_move_and_scroll_use_legacy_ranges(
    legacy_client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Legacy relative movement and scrolling use the signed HID ranges."""
    client, mock_ws = legacy_client_with_mock_ws

    await client.mouse_move_rel(0.1, -0.1)
    await client.mouse_scroll(0.1, -0.2)

    assert _sent_events(mock_ws) == [
        [2, 3, 0, 13, -13],
        [2, 4, 0, 0, -1],
    ]


async def test_mouse_click_releases_button_when_cancelled(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Cancelling the delay between mouse down and up still releases the button."""
    client, mock_ws = client_with_mock_ws
    sleep_started = asyncio.Event()

    async def block_sleep(_delay: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    with patch("nanokvm.components.mouse.asyncio.sleep", side_effect=block_sleep):
        task = asyncio.create_task(client.mouse_click())
        await sleep_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert _sent_reports(mock_ws) == [
        bytes([2, 1, 0, 0, 0]),
        bytes([2, 0, 0, 0, 0]),
    ]
    assert client._session._mouse_buttons == 0


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
            client._session._hw_version = HWVersion.PCIE
            client._session._application_version = "2.3.2"
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
            client._session._hw_version = HWVersion.PCIE
            client._session._application_version = "2.3.2"
            with pytest.raises(ConnectionResetError):
                await client.mouse_move_rel(0.1, 0.0)
            await client.mouse_move_rel(0.1, 0.0)

    assert connect.await_count == 2
    first_ws.close.assert_awaited_once()


async def test_legacy_mouse_send_reconnects_after_transport_failure() -> None:
    """A failed legacy send invalidates the cached WebSocket too."""
    first_ws = AsyncMock()
    first_ws.closed = False
    first_ws.send_json.side_effect = ConnectionResetError()
    second_ws = AsyncMock()
    second_ws.closed = False
    connect = AsyncMock(side_effect=[first_ws, second_ws])

    with patch("aiohttp.ClientSession.ws_connect", new=connect):
        async with NanoKVMClient(
            "http://localhost:8888/api/", token="test-token"
        ) as client:
            client._session._hw_version = HWVersion.PCIE
            client._session._application_version = "2.3.1"
            with pytest.raises(ConnectionResetError):
                await client.mouse_move_rel(0.1, 0.0)
            await client.mouse_move_rel(0.1, 0.0)

    assert connect.await_count == 2
    first_ws.close.assert_awaited_once()


async def test_mouse_send_helper_invalidates_failed_websocket() -> None:
    """The shared mouse transport helper invalidates the failed connection."""
    ws = AsyncMock()
    ws.closed = False

    async with NanoKVMClient(
        "http://localhost:8888/api/", token="test-token"
    ) as client:
        client._session._ws = ws

        async def send(current_ws: Any) -> None:
            assert current_ws is ws
            raise ConnectionResetError()

        with pytest.raises(ConnectionResetError):
            await client._mouse.send_ws(send)

    assert client._session._ws is None
    ws.close.assert_awaited_once()


async def test_logout_closes_websocket_and_prevents_further_mouse_input(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Logout invalidates the transport and local pressed-button state."""
    client, mock_ws = client_with_mock_ws
    await client.mouse_move_rel(0.1, 0.0)
    client._session._mouse_buttons = int(MouseButton.LEFT)

    with patch.object(client._session, "api_request_json", new_callable=AsyncMock):
        await client.logout()

    assert client.token is None
    assert client._session._mouse_buttons == 0
    mock_ws.close.assert_awaited_once()
    assert client._session._ws is None
    with pytest.raises(NanoKVMNotAuthenticatedError, match="not authenticated"):
        await client.mouse_move_rel(0.1, 0.0)


async def test_pro_mouse_uses_binary_protocol(
    client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Pro application versions from 1.2.6 use binary HID reports."""
    client, mock_ws = client_with_mock_ws
    client._session._hw_version = HWVersion.PRO
    client._session._application_version = "1.2.6"

    await client.mouse_move_abs(0.5, 0.5)

    assert _sent_reports(mock_ws) == [bytes([2, 0, 0, 64, 0, 64, 0])]


async def test_legacy_pro_mouse_uses_json_protocol(
    legacy_client_with_mock_ws: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Pro application versions before 1.2.6 use the legacy JSON protocol."""
    client, mock_ws = legacy_client_with_mock_ws
    client._session._hw_version = HWVersion.PRO
    client._session._application_version = "1.2.5"

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
            client._session._hw_version = HWVersion.PCIE
            client._session._application_version = "2.3.2"
            await client.mouse_move_abs(0.0, 0.0)
            assert client._session._ws is not None
            assert client._session._http_session is not None

        mock_ws.close.assert_called_once()
        assert client._session._ws is None
        assert client._session._http_session is None


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


@pytest.mark.parametrize("failure", ["forbidden", "connection", "cancelled"])
async def test_failed_press_does_not_press_on_next_movement(failure: str) -> None:
    """An unsent press must not turn later movement into a drag."""
    from multidict import CIMultiDict, CIMultiDictProxy
    import yarl

    from nanokvm.client import NanoKVMPermissionError

    info = aiohttp.RequestInfo(
        yarl.URL("http://kvm.local/api/ws"), "GET", CIMultiDictProxy(CIMultiDict())
    )
    error: BaseException
    expected: type[BaseException]
    if failure == "forbidden":
        error = aiohttp.WSServerHandshakeError(info, (), status=403)
        expected = NanoKVMPermissionError
    elif failure == "cancelled":
        error = asyncio.CancelledError()
        expected = asyncio.CancelledError
    else:
        error = aiohttp.ClientConnectionError("synthetic failure")
        expected = aiohttp.ClientConnectionError
    ws = AsyncMock(closed=False)
    async with NanoKVMClient("http://kvm.local/api/", token="reader") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.1"
        with patch(
            "aiohttp.ClientSession.ws_connect", AsyncMock(side_effect=[error, ws])
        ):
            with pytest.raises(expected):
                await client.mouse_down()
            await client.mouse_move_rel(0.1, 0)
            assert ws.send_bytes.call_args.args[0] == bytes((2, 0, 13, 0, 0))
