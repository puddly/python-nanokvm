"""Tests for user account management on non-Pro firmware 2.5.1 and newer."""

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import HWVersion, UserRole

from .common import BASE_URL, mark_detected

_OK = {"code": 0, "msg": "success", "data": None}


def _sent_json(mocked: aioresponses, method: str, path: str) -> dict[str, object]:
    calls = mocked.requests[(method, yarl.URL(f"{BASE_URL}{path}"))]
    body: dict[str, object] = calls[0].kwargs["json"]
    return body


async def test_get_users_parses_accounts(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The owner flag is optional and future roles stay readable."""
    mark_detected(client)
    mocked.get(
        f"{BASE_URL}auth/users",
        payload={
            "code": 0,
            "msg": "success",
            "data": {
                "users": [
                    {
                        "username": "owner",
                        "role": "admin",
                        "enabled": True,
                        "systemAccount": True,
                    },
                    {"username": "guest", "role": "future-role", "enabled": False},
                ]
            },
        },
    )

    owner, guest = (await client.get_users()).users

    assert owner.system_account and owner.role == UserRole.ADMIN
    assert not guest.system_account and not guest.enabled
    assert guest.role == "future-role"


async def test_create_user_sends_obfuscated_password(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The password never leaves the client in plain text."""
    mark_detected(client)
    mocked.post(f"{BASE_URL}auth/users", payload=_OK)

    await client.create_user("guest", "synthetic-password")

    body = _sent_json(mocked, "POST", "auth/users")
    assert body["username"] == "guest"
    assert body["role"] == "user"
    assert body["password"] != "synthetic-password"


async def test_update_user_sends_only_given_fields(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """An omitted field is left out so the device does not change it."""
    mark_detected(client)
    mocked.put(f"{BASE_URL}auth/users/guest", payload=_OK)

    await client.update_user("guest", enabled=False)

    assert _sent_json(mocked, "PUT", "auth/users/guest") == {"enabled": False}
    with pytest.raises(ValueError, match="role or enabled"):
        await client.update_user("guest")


@pytest.mark.parametrize("username", ["guest/password", "", ".."])
async def test_username_must_be_one_path_segment(
    client: NanoKVMClient, mocked: aioresponses, username: str
) -> None:
    """A name that would address another route is refused before any request."""
    mark_detected(client)

    with pytest.raises(ValueError, match="single path segment"):
        await client.delete_user(username)

    assert not mocked.requests


async def test_set_user_password_sends_obfuscated_password(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Resetting another user's password uses the per-user route."""
    mark_detected(client)
    mocked.post(f"{BASE_URL}auth/users/guest/password", payload=_OK)

    await client.set_user_password("guest", "synthetic-password")

    body = _sent_json(mocked, "POST", "auth/users/guest/password")
    assert set(body) == {"password"}
    assert body["password"] != "synthetic-password"


@pytest.mark.parametrize(
    ("hw_version", "application_version"),
    [(HWVersion.PCIE, "2.5.0"), (HWVersion.PRO, "9.9.9")],
)
async def test_users_require_non_pro_2_5_1(
    client: NanoKVMClient, hw_version: HWVersion, application_version: str
) -> None:
    """Older non-Pro firmware and Pro have no user accounts."""
    mark_detected(client, hw_version, application_version)

    with pytest.raises(NanoKVMNotSupportedError):
        await client.get_users()
