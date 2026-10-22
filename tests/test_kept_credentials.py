"""Tests for credentials kept in memory to log in again."""

from collections.abc import AsyncIterator

import aiohttp
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import (
    NanoKVMAuthenticationFailure,
    NanoKVMClient,
    NanoKVMNotAuthenticatedError,
)
from nanokvm.components.session import _Credentials
from nanokvm.models import ApiResponseCode, HWVersion

from .common import BASE_URL, mark_detected

_LOGIN_URL = f"{BASE_URL}auth/login"
_HARDWARE_URL = f"{BASE_URL}vm/hardware"
_USERNAME = "synthetic-user"
_PASSWORD = "synthetic-password"


def _ok(data: object = None) -> dict[str, object]:
    return {"code": 0, "msg": "success", "data": data}


_REJECTED = {
    "code": ApiResponseCode.INVALID_USERNAME_OR_PASSWORD.value,
    "msg": "invalid username or password",
    "data": None,
}


def _mock_login(mocked: aioresponses, token: str) -> None:
    mocked.post(_LOGIN_URL, payload=_ok({"token": token}))


def _login_bodies(mocked: aioresponses) -> list[object]:
    return [
        call.kwargs["json"]
        for call in mocked.requests.get(("POST", yarl.URL(_LOGIN_URL)), [])
    ]


@pytest.fixture
async def kept_client(mocked: aioresponses) -> AsyncIterator[NanoKVMClient]:
    """A client that kept the credentials of its login."""
    async with NanoKVMClient(
        BASE_URL, keep_credentials=True, use_password_obfuscation=False
    ) as client:
        _mock_login(mocked, "first-token")
        mocked.get(_HARDWARE_URL, payload=_ok({"version": "PCIE"}))
        await client.authenticate(_USERNAME, _PASSWORD)
        yield client


async def test_credentials_are_not_kept_by_default(mocked: aioresponses) -> None:
    """Without the opt-in, the client never keeps the password."""
    async with NanoKVMClient(BASE_URL, use_password_obfuscation=False) as client:
        _mock_login(mocked, "token")
        mocked.get(_HARDWARE_URL, payload=_ok({"version": "PCIE"}))
        await client.authenticate(_USERNAME, _PASSWORD)

        assert client._session._credentials is None
        with pytest.raises(NanoKVMNotAuthenticatedError, match="No credentials"):
            await client._session.reauthenticate()


async def test_reauthenticate_logs_in_with_kept_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A kept login is replayed and the new token replaces the old one."""
    _mock_login(mocked, "second-token")

    await kept_client._session.reauthenticate()

    assert kept_client.token == "second-token"
    assert _login_bodies(mocked) == [
        {"username": _USERNAME, "password": _PASSWORD},
        {"username": _USERNAME, "password": _PASSWORD},
    ]


def test_kept_password_is_hidden_from_repr() -> None:
    """Debug output never shows the kept password."""
    text = repr(_Credentials(_USERNAME, _PASSWORD))

    assert _USERNAME in text
    assert _PASSWORD not in text


async def test_unreachable_device_keeps_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A login that cannot reach the device can be retried later."""
    mocked.post(_LOGIN_URL, exception=aiohttp.ClientConnectionError())
    with pytest.raises(aiohttp.ClientConnectionError):
        await kept_client._session.reauthenticate()

    _mock_login(mocked, "second-token")
    await kept_client._session.reauthenticate()
    assert kept_client.token == "second-token"


async def test_rejected_credentials_are_dropped(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Credentials the device rejects are not tried again."""
    mocked.post(_LOGIN_URL, payload=_REJECTED)

    with pytest.raises(NanoKVMAuthenticationFailure):
        await kept_client._session.reauthenticate()

    assert kept_client._session._credentials is None


async def test_failed_login_drops_the_previous_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A failed switch to another account keeps neither account."""
    mocked.post(_LOGIN_URL, payload=_REJECTED)

    with pytest.raises(NanoKVMAuthenticationFailure):
        await kept_client.authenticate("other-user", "wrong-password")

    assert kept_client._session._credentials is None


async def test_logout_drops_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Logging out forgets the kept credentials."""
    mocked.post(f"{BASE_URL}auth/logout", payload=_ok())

    await kept_client.logout()

    assert kept_client._session._credentials is None


async def test_password_change_drops_credentials(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The old password is not kept after it changes."""
    mark_detected(kept_client, HWVersion.PCIE, "2.4.3")
    mocked.post(f"{BASE_URL}auth/password", payload=_ok())

    await kept_client.change_password(_USERNAME, "new-synthetic-password")

    assert kept_client._session._credentials is None


async def test_closing_the_client_drops_credentials(mocked: aioresponses) -> None:
    """Credentials do not outlive the client."""
    async with NanoKVMClient(
        BASE_URL, keep_credentials=True, use_password_obfuscation=False
    ) as client:
        _mock_login(mocked, "token")
        mocked.get(_HARDWARE_URL, payload=_ok({"version": "PCIE"}))
        await client.authenticate(_USERNAME, _PASSWORD)
        assert client._session._credentials is not None

    assert client._session._credentials is None
