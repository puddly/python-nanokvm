"""Tests for keyboard input and the shared input WebSocket."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, call, patch

import pytest

from nanokvm.client import NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import HWVersion, KeyCode, KeyModifier

from .common import BASE_URL, mark_detected

_RELEASE = bytes([1, 0, 0, 0, 0, 0, 0, 0, 0])
_HEARTBEAT = b"\x00"


def _mock_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.closed = False
    return ws


@pytest.fixture
async def ws_client(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[tuple[NanoKVMClient, AsyncMock], Any]:
    """A client on the hardware and version given by ``request.param``."""
    hw_version, version = getattr(request, "param", (HWVersion.PCIE, "2.3.2"))
    mock_ws = _mock_ws()

    with patch(
        "aiohttp.ClientSession.ws_connect", new_callable=AsyncMock, return_value=mock_ws
    ):
        async with NanoKVMClient(BASE_URL, token="test-token") as client:
            mark_detected(client, hw_version, version)
            yield client, mock_ws


def _sent_reports(mock_ws: AsyncMock) -> list[bytes]:
    return [
        call.args[0]
        for call in mock_ws.send_bytes.call_args_list
        if call.args[0] != _HEARTBEAT
    ]


def _sent_events(mock_ws: AsyncMock) -> list[list[int]]:
    return [call.args[0] for call in mock_ws.send_json.call_args_list]


async def test_key_down_and_up_send_boot_keyboard_reports(
    ws_client: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Binary reports carry the modifier byte, a reserved byte and six keys."""
    client, mock_ws = ws_client

    await client.key_down(
        KeyCode.DELETE, modifiers=KeyModifier.LEFT_CTRL | KeyModifier.LEFT_ALT
    )
    await client.key_down(*range(4, 10), modifiers=KeyModifier.RIGHT_META)
    await client.key_down(modifiers=KeyModifier.LEFT_SHIFT)
    await client.key_up()

    assert _sent_reports(mock_ws) == [
        bytes([1, 0x05, 0, 0x4C, 0, 0, 0, 0, 0]),
        bytes([1, 0x80, 0, 4, 5, 6, 7, 8, 9]),
        bytes([1, 0x02, 0, 0, 0, 0, 0, 0, 0]),
        _RELEASE,
    ]
    mock_ws.send_json.assert_not_called()


@pytest.mark.parametrize(
    ("keys", "modifiers"),
    [(tuple(range(4, 11)), 0), ((256,), 0), ((-1,), 0), ((), 256)],
)
async def test_invalid_reports_are_rejected_before_sending(
    ws_client: tuple[NanoKVMClient, AsyncMock],
    keys: tuple[int, ...],
    modifiers: int,
) -> None:
    """More than six keys or values outside a byte never reach the device."""
    client, mock_ws = ws_client

    with pytest.raises(ValueError):
        await client.key_down(*keys, modifiers=modifiers)

    mock_ws.send_bytes.assert_not_called()


async def test_key_tap_presses_and_releases(
    ws_client: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """A tap holds the keys for ``hold`` seconds, then releases them."""
    client, mock_ws = ws_client

    # Spy on the real sleep: a mock that never yields would spin the heartbeat.
    with patch(
        "nanokvm.components.hid.asyncio.sleep", side_effect=asyncio.sleep
    ) as sleep:
        await client.key_tap(KeyCode.F12, modifiers=KeyModifier.LEFT_SHIFT, hold=0.01)

    assert call(0.01) in sleep.await_args_list
    assert _sent_reports(mock_ws) == [
        bytes([1, 0x02, 0, 0x45, 0, 0, 0, 0, 0]),
        _RELEASE,
    ]


async def test_cancelled_key_tap_still_releases(
    ws_client: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Cancelling a tap while the key is held does not leave it held."""
    client, mock_ws = ws_client

    tap = asyncio.create_task(client.key_tap(KeyCode.ENTER, hold=60))
    async with asyncio.timeout(2):
        while not _sent_reports(mock_ws):
            await asyncio.sleep(0)
    tap.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tap

    assert _sent_reports(mock_ws) == [
        bytes([1, 0, 0, 0x28, 0, 0, 0, 0, 0]),
        _RELEASE,
    ]


@pytest.mark.parametrize("hold", [-0.1, float("nan"), float("inf")])
async def test_key_tap_rejects_invalid_hold(
    ws_client: tuple[NanoKVMClient, AsyncMock], hold: float
) -> None:
    """An invalid hold time is rejected before any key is pressed."""
    client, mock_ws = ws_client

    with pytest.raises(ValueError, match="hold"):
        await client.key_tap(KeyCode.KEY_A, hold=hold)

    mock_ws.send_bytes.assert_not_called()


@pytest.mark.parametrize("ws_client", [(HWVersion.PCIE, "2.3.1")], indirect=True)
async def test_legacy_firmware_receives_json_key_events(
    ws_client: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Before 2.3.2, keys are JSON events whose modifier fields are ORed."""
    client, mock_ws = ws_client

    await client.key_down(
        KeyCode.KEY_A, modifiers=KeyModifier.LEFT_CTRL | KeyModifier.RIGHT_SHIFT
    )
    await client.key_up()

    assert _sent_events(mock_ws) == [[1, 0x04, 0x21, 0, 0, 0], [1, 0, 0, 0, 0, 0]]
    assert not _sent_reports(mock_ws)


@pytest.mark.parametrize("ws_client", [(HWVersion.PCIE, "2.3.1")], indirect=True)
@pytest.mark.parametrize(
    ("keys", "modifiers"),
    [((KeyCode.KEY_A, KeyCode.KEY_B), 0), ((), KeyModifier.LEFT_SHIFT)],
)
async def test_legacy_firmware_rejects_reports_it_cannot_express(
    ws_client: tuple[NanoKVMClient, AsyncMock],
    keys: tuple[KeyCode, ...],
    modifiers: KeyModifier,
) -> None:
    """The legacy format has one key slot and drops modifiers without a key."""
    client, mock_ws = ws_client

    with pytest.raises(NanoKVMNotSupportedError, match="binary keyboard protocol"):
        await client.key_down(*keys, modifiers=modifiers)

    mock_ws.send_json.assert_not_called()


@pytest.mark.parametrize(
    ("ws_client", "binary"),
    [
        ((HWVersion.PCIE, "2.3.2"), True),
        ((HWVersion.BETA, "2.3.1"), False),
        ((HWVersion.PRO, "1.2.6"), True),
        ((HWVersion.PRO, "1.2.5"), False),
    ],
    indirect=["ws_client"],
)
async def test_keyboard_protocol_follows_firmware_version(
    ws_client: tuple[NanoKVMClient, AsyncMock], binary: bool
) -> None:
    """Keyboard and mouse switch to binary reports at the same versions."""
    client, mock_ws = ws_client

    await client.key_up()

    assert bool(_sent_reports(mock_ws)) is binary
    assert bool(_sent_events(mock_ws)) is not binary


async def test_shared_websocket_sends_heartbeats_until_closed(
    ws_client: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """An idle input WebSocket sends heartbeats, which stop when it closes."""
    client, mock_ws = ws_client

    with patch("nanokvm.components.session._WS_HEARTBEAT_INTERVAL", 0):
        await client.key_up()
        heartbeat = client._session._ws_heartbeat
        assert heartbeat is not None
        async with asyncio.timeout(2):
            while mock_ws.send_bytes.await_args_list[-1].args[0] != _HEARTBEAT:
                await asyncio.sleep(0)

        await client._session.close_ws()

    assert client._session._ws_heartbeat is None
    with pytest.raises(asyncio.CancelledError):
        await heartbeat


async def test_logout_stops_the_heartbeat(
    ws_client: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """Clearing the session stops the heartbeat with the WebSocket."""
    client, _ = ws_client
    await client.key_up()
    heartbeat = client._session._ws_heartbeat
    assert heartbeat is not None

    await client._session.clear_local_session()

    assert client._session._ws_heartbeat is None
    with pytest.raises(asyncio.CancelledError):
        await heartbeat


async def test_failed_heartbeat_ends_quietly(
    ws_client: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """A broken connection ends the heartbeat; the next input reconnects."""
    client, mock_ws = ws_client
    await client.key_up()
    mock_ws.send_bytes.side_effect = ConnectionResetError()

    with patch("nanokvm.components.session._WS_HEARTBEAT_INTERVAL", 0):
        heartbeat = asyncio.create_task(client._session._send_heartbeats(mock_ws))
        async with asyncio.timeout(2):
            await heartbeat

    assert heartbeat.result() is None


async def test_replacing_a_closed_websocket_restarts_the_heartbeat(
    ws_client: tuple[NanoKVMClient, AsyncMock],
) -> None:
    """A WebSocket closed by the device is replaced with a new heartbeat."""
    client, mock_ws = ws_client
    await client.key_up()
    first_heartbeat = client._session._ws_heartbeat
    mock_ws.closed = True
    second_ws = _mock_ws()

    with patch(
        "aiohttp.ClientSession.ws_connect",
        new_callable=AsyncMock,
        return_value=second_ws,
    ):
        await client.key_up()

    assert client._session._ws is second_ws
    assert client._session._ws_heartbeat not in (None, first_heartbeat)
    assert _sent_reports(second_ws) == [_RELEASE]
    assert first_heartbeat is not None
    with pytest.raises(asyncio.CancelledError):
        await first_heartbeat
