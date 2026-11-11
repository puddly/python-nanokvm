"""Internal user account operations for the NanoKVM session."""

from __future__ import annotations

from aiohttp import hdrs

from ..compatibility import require_hardware
from ..exceptions import NanoKVMNotSupportedError
from ..models.common import HWFamily
from ..models.non_pro import (
    CreateUserReq,
    GetPermissionsRsp,
    GetUsersRsp,
    Permission,
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

    async def _require_permissions_api(self) -> None:
        if not await self._session.application_version_at_least(non_pro="2.5.3"):
            raise NanoKVMNotSupportedError(
                "account permissions require non-Pro application version >= 2.5.3 "
                f"(detected: {self._session._application_version})"
            )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def get_permissions(self) -> GetPermissionsRsp:
        """List the permissions an account can be granted."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/auth/permissions",
            response_model=GetPermissionsRsp,
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.1")
    async def get_users(self) -> GetUsersRsp:
        """List the user accounts on the device."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/auth/users",
            response_model=GetUsersRsp,
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.1")
    async def create_user(
        self,
        username: str,
        password: str,
        role: UserRole | str = UserRole.USER,
        *,
        permissions: list[Permission | str] | None = None,
    ) -> None:
        """Create a user account.

        ``permissions`` needs 2.5.3. Without it, a user account gets console
        view, console control and power; an empty list grants nothing.
        """
        if permissions is not None:
            await self._require_permissions_api()
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/auth/users",
            data=CreateUserReq(
                username=username,
                password=obfuscate_password(password),
                role=role,
                permissions=permissions,
            ),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.1")
    async def update_user(
        self,
        username: str,
        *,
        role: UserRole | str | None = None,
        enabled: bool | None = None,
        permissions: list[Permission | str] | None = None,
        rotate_mcp_key: bool = False,
    ) -> None:
        """Change a user's role, enabled state, permissions or several.

        Before 2.5.3 the device ends that user's sessions; from 2.5.3 only
        disabling does. ``permissions`` (2.5.3) replaces the stored grants.
        ``rotate_mcp_key`` (2.5.3) regenerates the device MCP API key if the
        change takes the ``mcp`` grant away, which disconnects every MCP client.
        """
        if role is None and enabled is None and permissions is None:
            raise ValueError("role, enabled or permissions is required")
        if permissions is not None or rotate_mcp_key:
            await self._require_permissions_api()
        await self._session.api_request_json(
            hdrs.METH_PUT,
            _user_path(username),
            data=UpdateUserReq(
                role=role,
                enabled=enabled,
                permissions=permissions,
                rotate_mcp_key=rotate_mcp_key or None,
            ),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.1")
    async def delete_user(self, username: str, *, rotate_mcp_key: bool = False) -> None:
        """Delete a user account and end its sessions.

        ``rotate_mcp_key`` (2.5.3) regenerates the device MCP API key if the
        account held the ``mcp`` grant.
        """
        params = {}
        if rotate_mcp_key:
            await self._require_permissions_api()
            params["rotateMcpKey"] = "true"
        await self._session.api_request_json(
            hdrs.METH_DELETE, _user_path(username), params=params
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.1")
    async def set_user_password(self, username: str, new_password: str) -> None:
        """Set another user's password and end that user's sessions.

        The device owner account is refused; it uses ``change_password``.
        """
        await self._session.api_request_json(
            hdrs.METH_POST,
            _user_path(username, "/password"),
            data=SetUserPasswordReq(password=obfuscate_password(new_password)),
        )
