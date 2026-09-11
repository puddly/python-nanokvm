"""Regression tests for NanoKVM 2.5.1 self-service password changes."""

from unittest.mock import AsyncMock, patch

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMApiError, NanoKVMClient, NanoKVMError
from nanokvm.models import HWVersion

_BASE_URL = "http://kvm.local/api/"
_ACCOUNT_URL = f"{_BASE_URL}auth/account"
_PASSWORD_URL = f"{_BASE_URL}auth/password"


def _account_payload(username: str = "synthetic-user") -> dict[str, object]:
    return {
        "code": 0,
        "msg": "success",
        "data": {"username": username, "role": "user"},
    }


def _success_payload() -> dict[str, object]:
    return {"code": 0, "msg": "success", "data": None}


def _prepare_client(
    *,
    hardware: HWVersion = HWVersion.PCIE,
    application: str | None = "2.5.1",
) -> NanoKVMClient:
    client = NanoKVMClient(
        _BASE_URL,
        token="session-token",
        use_password_obfuscation=True,
    )
    client._hw_version = hardware
    client._application_version = application
    return client


async def test_new_contract_sends_both_obfuscated_passwords_and_clears_session() -> (
    None
):
    """2.5.1 changes the authenticated user's password and invalidates sessions."""
    client = _prepare_client()
    client._use_password_obfuscation = False
    mock_ws = AsyncMock()
    mock_ws.closed = False

    async with client:
        client._ws = mock_ws
        client._mouse_buttons = 1
        with (
            aioresponses() as mocked,
            patch(
                "nanokvm.client.obfuscate_password",
                side_effect=lambda value: f"encoded-{value}",
            ),
        ):
            mocked.get(_ACCOUNT_URL, payload=_account_payload())
            mocked.post(_PASSWORD_URL, payload=_success_payload())

            await client.change_password(
                "synthetic-user",
                "new-password",
                current_password="current-password",
            )

            calls = mocked.requests[("POST", yarl.URL(_PASSWORD_URL))]
            assert calls[0].kwargs.get("json") == {
                "currentPassword": "encoded-current-password",
                "password": "encoded-new-password",
            }
            assert client.token is None
            assert client._mouse_buttons == 0
            mock_ws.close.assert_awaited_once()
            assert client._ws is None


async def test_new_contract_requires_current_password_before_sending() -> None:
    """A 2.5.1 change cannot be attempted without the current password."""
    async with _prepare_client() as client:
        with pytest.raises(ValueError, match="current_password"):
            await client.change_password("synthetic-user", "new-password")


async def test_new_contract_rejects_a_different_authenticated_username() -> None:
    """The body username cannot be used to change another account's password."""
    async with _prepare_client() as client:
        with aioresponses() as mocked:
            mocked.get(_ACCOUNT_URL, payload=_account_payload("authenticated-user"))

            with pytest.raises(ValueError, match="authenticated account"):
                await client.change_password(
                    "other-user",
                    "new-password",
                    current_password="current-password",
                )

            assert ("POST", yarl.URL(_PASSWORD_URL)) not in mocked.requests


async def test_new_contract_preserves_api_error_codes() -> None:
    """An incorrect current password remains a device API error."""
    async with _prepare_client() as client:
        with aioresponses() as mocked:
            mocked.get(_ACCOUNT_URL, payload=_account_payload())
            mocked.post(
                _PASSWORD_URL,
                payload={"code": -3, "msg": "current password incorrect", "data": None},
            )

            with pytest.raises(NanoKVMApiError) as exc_info:
                await client.change_password(
                    "synthetic-user",
                    "new-password",
                    current_password="wrong-password",
                )

            assert exc_info.value.code == -3
            assert client.token == "session-token"


@pytest.mark.parametrize(
    ("hardware", "application"),
    [
        (HWVersion.PCIE, "2.5.0"),
        (HWVersion.PRO, "1.2.15"),
    ],
)
async def test_legacy_contract_is_preserved_for_old_and_pro_devices(
    hardware: HWVersion,
    application: str,
) -> None:
    """Older non-Pro firmware and Pro keep the username/password request."""
    async with _prepare_client(hardware=hardware, application=application) as client:
        with (
            aioresponses() as mocked,
            patch(
                "nanokvm.client.obfuscate_password",
                side_effect=lambda value: f"encoded-{value}",
            ),
        ):
            mocked.post(_PASSWORD_URL, payload=_success_payload())

            await client.change_password("synthetic-user", "new-password")

            calls = mocked.requests[("POST", yarl.URL(_PASSWORD_URL))]
            assert calls[0].kwargs.get("json") == {
                "username": "synthetic-user",
                "password": "encoded-new-password",
            }
            assert ("GET", yarl.URL(_ACCOUNT_URL)) not in mocked.requests


async def test_legacy_contract_preserves_plain_text_password_mode() -> None:
    """The old contract still honors an explicitly selected plain mode."""
    client = _prepare_client(hardware=HWVersion.PCIE, application="2.5.0")
    client._use_password_obfuscation = False

    async with client:
        with aioresponses() as mocked:
            mocked.post(_PASSWORD_URL, payload=_success_payload())

            await client.change_password("synthetic-user", "new-password")

            calls = mocked.requests[("POST", yarl.URL(_PASSWORD_URL))]
            assert calls[0].kwargs.get("json") == {
                "username": "synthetic-user",
                "password": "new-password",
            }


async def test_unknown_application_version_is_not_guessed() -> None:
    """A missing version produces an actionable error instead of guessing."""
    async with _prepare_client(application=None) as client:
        with patch.object(
            client, "detect_versions", new_callable=AsyncMock
        ) as detect_versions:
            with pytest.raises(NanoKVMError, match="Application version"):
                await client.change_password(
                    "synthetic-user",
                    "new-password",
                    current_password="current-password",
                )

            detect_versions.assert_awaited_once()


async def test_custom_application_version_is_not_guessed() -> None:
    """A development version without a comparable number uses neither contract."""
    async with _prepare_client(application="development") as client:
        with pytest.raises(NanoKVMError, match="Application version"):
            await client.change_password(
                "synthetic-user",
                "new-password",
                current_password="current-password",
            )


async def test_password_change_detects_hardware_and_version_when_needed() -> None:
    """An authenticated client can identify the contract through /vm endpoints."""
    async with _prepare_client(hardware=HWVersion.UNKNOWN, application=None) as client:
        client._hw_version = None
        with (
            aioresponses() as mocked,
            patch(
                "nanokvm.client.obfuscate_password",
                side_effect=lambda value: f"encoded-{value}",
            ),
        ):
            mocked.get(
                f"{_BASE_URL}vm/hardware",
                payload={"code": 0, "msg": "success", "data": {"version": "PCIE"}},
            )
            mocked.get(
                f"{_BASE_URL}vm/info",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "ips": [],
                        "mdns": "kvm.local",
                        "image": "v1.4.0",
                        "application": "2.5.1",
                        "deviceKey": "synthetic-device-key",
                    },
                },
            )
            mocked.get(_ACCOUNT_URL, payload=_account_payload())
            mocked.post(_PASSWORD_URL, payload=_success_payload())

            await client.change_password(
                "synthetic-user",
                "new-password",
                current_password="current-password",
            )

            assert client.token is None
