"""Tests for endpoint hardware and application version compatibility."""

from typing import Any
from unittest.mock import AsyncMock, patch

from aioresponses import aioresponses
import pytest

from nanokvm.client import NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import HWVersion

_BASE_URL = "http://localhost:8888/api/"

_VERSIONED_METHODS: list[tuple[str, tuple[Any, ...], HWVersion, str, str]] = [
    ("get_ssh_state", (), HWVersion.PCIE, "2.1.5", "2.1.6"),
    ("enable_ssh", (), HWVersion.PCIE, "2.1.5", "2.1.6"),
    ("disable_ssh", (), HWVersion.PCIE, "2.1.5", "2.1.6"),
    ("get_tailscale_status", (), HWVersion.PCIE, "2.1.5", "2.1.6"),
    ("get_cdrom_status", (), HWVersion.PCIE, "2.2.0", "2.2.1"),
    (
        "connect_wifi",
        ("Test Network", "test-password"),
        HWVersion.PCIE,
        "2.3.0",
        "2.3.1",
    ),
    ("disconnect_wifi", (), HWVersion.PCIE, "2.3.0", "2.3.1"),
    ("assistant_install", (), HWVersion.PRO, "1.1.3", "1.1.4"),
    ("assistant_start", (), HWVersion.PRO, "1.1.3", "1.1.4"),
    ("kvmadmin_install", (), HWVersion.PRO, "1.1.4", "1.1.5"),
    ("kvmadmin_uninstall", (), HWVersion.PRO, "1.1.4", "1.1.5"),
    ("kvmadmin_start", (), HWVersion.PRO, "1.1.4", "1.1.5"),
    ("kvmadmin_stop", (), HWVersion.PRO, "1.1.4", "1.1.5"),
    ("kvmadmin_status", (), HWVersion.PRO, "1.1.4", "1.1.5"),
    ("reset_hid", (), HWVersion.PRO, "1.1.5", "1.1.6"),
    ("delete_image", ("test.iso",), HWVersion.PRO, "1.1.5", "1.1.6"),
    ("get_timezone", (), HWVersion.PRO, "1.1.5", "1.1.6"),
    ("set_timezone", ("Etc/UTC",), HWVersion.PRO, "1.1.5", "1.1.6"),
]


@pytest.mark.parametrize(
    ("method_name", "args", "hardware", "old_version", "minimum_version"),
    _VERSIONED_METHODS,
)
async def test_methods_reject_unsupported_application_versions(
    method_name: str,
    args: tuple[Any, ...],
    hardware: HWVersion,
    old_version: str,
    minimum_version: str,
) -> None:
    """Endpoints fail locally when the detected application is too old."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = hardware
        client._application_version = old_version

        with patch.object(
            client, "_api_request_json", new_callable=AsyncMock
        ) as request:
            with pytest.raises(
                NanoKVMNotSupportedError,
                match=rf"{method_name} requires .* version >= {minimum_version}",
            ):
                await getattr(client, method_name)(*args)

            request.assert_not_awaited()


@pytest.mark.parametrize(
    ("method_name", "args", "hardware", "_old_version", "minimum_version"),
    _VERSIONED_METHODS,
)
async def test_methods_accept_first_supported_application_version(
    method_name: str,
    args: tuple[Any, ...],
    hardware: HWVersion,
    _old_version: str,
    minimum_version: str,
) -> None:
    """Endpoints delegate at the first supported application version."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = hardware
        client._application_version = minimum_version

        with patch.object(
            client, "_api_request_json", new_callable=AsyncMock
        ) as request:
            await getattr(client, method_name)(*args)

            request.assert_awaited_once()


async def test_logout_ignores_missing_legacy_endpoint() -> None:
    """Legacy devices without a logout route still clear the local session."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        with aioresponses() as mocked:
            mocked.post(f"{_BASE_URL}auth/logout", status=404)

            await client.logout()

        assert client.token is None
