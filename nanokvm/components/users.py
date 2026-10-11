"""Internal user account operations for the NanoKVM session."""

from __future__ import annotations

from aiohttp import hdrs

from ..compatibility import require_application_version, require_hardware
from ..models.common import HWFamily
from ..models.non_pro import (
    CreateUserReq,
    GetUsersRsp,
    SetUserPasswordReq,
    UpdateUserReq,
    UserRole,
)
from ..utils import obfuscate_password
from .session import Controller


def _user_path(username: str, suffix: str = "") -> str:
    # The name is one path segment; anything else would reach another route.
    if username in ("", ".", "..") or "/" in username:
        raise ValueError("username must be a single path segment")
    return f"/auth/users/{username}{suffix}"


class UserController(Controller):
    """Implement user account operations behind the public client facade."""

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def get_users(self) -> GetUsersRsp:
        """List the user accounts on the device."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/auth/users",
            response_model=GetUsersRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def create_user(
        self, username: str, password: str, role: UserRole | str = UserRole.USER
    ) -> None:
        """Create a user account."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/auth/users",
            data=CreateUserReq(
                username=username,
                password=obfuscate_password(password),
                role=role,
            ),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def update_user(
        self,
        username: str,
        *,
        role: UserRole | str | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Change a user's role, enabled state or both.

        The device ends that user's sessions.
        """
        if role is None and enabled is None:
            raise ValueError("role or enabled is required")
        await self._session.api_request_json(
            hdrs.METH_PUT,
            _user_path(username),
            data=UpdateUserReq(role=role, enabled=enabled),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def delete_user(self, username: str) -> None:
        """Delete a user account and end its sessions."""
        await self._session.api_request_json(hdrs.METH_DELETE, _user_path(username))

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def set_user_password(self, username: str, new_password: str) -> None:
        """Set another user's password and end that user's sessions.

        The device owner account is refused; it uses ``change_password``.
        """
        await self._session.api_request_json(
            hdrs.METH_POST,
            _user_path(username, "/password"),
            data=SetUserPasswordReq(password=obfuscate_password(new_password)),
        )
