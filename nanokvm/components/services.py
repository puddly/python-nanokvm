"""Internal services operations for the NanoKVM client."""

from __future__ import annotations

import asyncio
import hashlib
from os import PathLike
from pathlib import Path
import re
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import hdrs

from ..compatibility import _version_at_least
from ..models.common import (
    GetPreviewRsp,
    GetTailscaleStatusRsp,
    GetVersionRsp,
    LoginTailscaleRsp,
    SetPreviewReq,
)
from ..models.non_pro import (
    AIControlMode,
    AIControlStatusRsp,
    GetMCPConfigRsp,
    GetUpdateServerRsp,
    SetAIControlModeReq,
    SetAIControlModeRsp,
    SetMCPConfigReq,
    SetUpdateServerReq,
)
from ..models.pro import GetKvmadminStatusRsp

if TYPE_CHECKING:
    from ..client import NanoKVMClient


_OFFLINE_UPDATE_PACKAGE_RE = re.compile(r"^nanokvm_[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$")
_OFFLINE_UPDATE_TIMEOUT_SECONDS = 15 * 60


def _calculate_file_sha256(file_path: Path) -> str:
    """Calculate a file checksum without loading it into memory."""
    with file_path.open("rb") as file_obj:
        return hashlib.file_digest(file_obj, "sha256").hexdigest()


class ServiceController:
    """Implement services operations behind the public client facade."""

    def __init__(self, client: NanoKVMClient) -> None:
        self._client = client

    async def get_tailscale_status(self) -> GetTailscaleStatusRsp:
        """Get Tailscale status."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/extensions/tailscale/status",
            response_model=GetTailscaleStatusRsp,
        )

    async def get_application_version(self) -> GetVersionRsp:
        """Get current and latest application versions."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/application/version",
            response_model=GetVersionRsp,
        )

    async def get_preview_status(self) -> GetPreviewRsp:
        """Check if preview updates are enabled."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/application/preview",
            response_model=GetPreviewRsp,
        )

    async def set_preview_state(self, enable: bool) -> None:
        """Enable or disable preview updates."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/application/preview",
            data=SetPreviewReq(enable=enable),
        )

    async def update_application(self) -> None:
        """Trigger the application update process."""
        await self._client._api_request_json(hdrs.METH_POST, "/application/update")

    async def update_application_offline(
        self,
        file_path: str | PathLike[str],
        *,
        sha256: str | None = None,
    ) -> None:
        """Install an official non-Pro application package from a local file."""
        package_path = Path(file_path)
        if _OFFLINE_UPDATE_PACKAGE_RE.fullmatch(package_path.name) is None:
            raise ValueError("Update package filename must match nanokvm_X.Y.Z.tar.gz")

        checksum = sha256.strip() if sha256 is not None else ""
        if checksum and re.fullmatch(r"[0-9a-fA-F]{64}", checksum) is None:
            raise ValueError("SHA-256 checksum must contain exactly 64 hex characters")

        if checksum:
            actual_checksum = await asyncio.to_thread(
                _calculate_file_sha256, package_path
            )
            if actual_checksum != checksum.lower():
                raise ValueError("SHA-256 checksum does not match the update package")

        supports_remote_checksum = (
            self._client._application_version is None
            or _version_at_least(self._client._application_version, "2.5.1")
        )
        headers = (
            {"X-SHA256-Checksum": checksum}
            if checksum and supports_remote_checksum
            else {}
        )
        try:
            await self._client._upload_file(
                "/application/update/offline",
                package_path,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=_OFFLINE_UPDATE_TIMEOUT_SECONDS),
            )
        except aiohttp.ClientResponseError as err:
            # The device can stop its old HTTP service before the proxy has
            # forwarded the successful response. Its official UI treats that
            # restart-related gateway response as successful too.
            if (
                err.status != 502
                or self._client._application_version is None
                or not _version_at_least(self._client._application_version, "2.5.1")
            ):
                raise

    async def get_update_server(self) -> GetUpdateServerRsp:
        """Get the custom application update-server configuration."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/application/update-server",
            response_model=GetUpdateServerRsp,
        )

    async def set_update_server(
        self,
        enabled: bool,
        url: str,
    ) -> GetUpdateServerRsp:
        """Set and return the custom application update-server configuration."""
        return await self._client._api_request_json(
            hdrs.METH_POST,
            "/application/update-server",
            response_model=GetUpdateServerRsp,
            data=SetUpdateServerReq(enabled=enabled, url=url),
        )

    async def get_mcp_config(self) -> GetMCPConfigRsp:
        """Get MCP configuration and coordinated-control state."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/mcp/config",
            response_model=GetMCPConfigRsp,
        )

    async def set_mcp_enabled(self, enabled: bool) -> GetMCPConfigRsp:
        """Enable or disable MCP control."""
        return await self._client._api_request_json(
            hdrs.METH_POST,
            "/mcp/config",
            response_model=GetMCPConfigRsp,
            data=SetMCPConfigReq(enabled=enabled),
        )

    async def regenerate_mcp_api_key(self) -> GetMCPConfigRsp:
        """Generate and return a new MCP API key."""
        return await self._client._api_request_json(
            hdrs.METH_POST,
            "/mcp/key/regenerate",
            response_model=GetMCPConfigRsp,
        )

    async def get_ai_control_status(self) -> AIControlStatusRsp:
        """Get the current coordinated-control owner and transition state."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/ai/control/status",
            response_model=AIControlStatusRsp,
        )

    async def set_ai_control_mode(self, mode: AIControlMode) -> SetAIControlModeRsp:
        """Select the owner of coordinated input control."""
        return await self._client._api_request_json(
            hdrs.METH_PUT,
            "/ai/control/mode",
            response_model=SetAIControlModeRsp,
            data=SetAIControlModeReq(mode=mode),
        )

    async def tailscale_install(self) -> None:
        """Install Tailscale."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/install"
        )

    async def tailscale_uninstall(self) -> None:
        """Uninstall Tailscale."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/uninstall"
        )

    async def tailscale_up(self) -> None:
        """Bring Tailscale up."""
        await self._client._api_request_json(hdrs.METH_POST, "/extensions/tailscale/up")

    async def tailscale_down(self) -> None:
        """Bring Tailscale down."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/down"
        )

    async def tailscale_login(self) -> LoginTailscaleRsp:
        """Log in to Tailscale."""
        return await self._client._api_request_json(
            hdrs.METH_POST,
            "/extensions/tailscale/login",
            response_model=LoginTailscaleRsp,
        )

    async def tailscale_logout(self) -> None:
        """Log out of Tailscale."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/logout"
        )

    async def tailscale_start(self) -> None:
        """Start Tailscale service."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/start"
        )

    async def tailscale_stop(self) -> None:
        """Stop Tailscale service."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/stop"
        )

    async def tailscale_restart(self) -> None:
        """Restart Tailscale service."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/restart"
        )

    async def assistant_install(self) -> None:
        """Install assistant dependencies."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/assistant/install"
        )

    async def assistant_start(self) -> None:
        """Start assistant."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/assistant/start"
        )

    async def kvmadmin_install(self) -> None:
        """Install kvmadmin."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/kvmadmin/install"
        )

    async def kvmadmin_uninstall(self) -> None:
        """Uninstall kvmadmin."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/kvmadmin/uninstall"
        )

    async def kvmadmin_start(self) -> None:
        """Start kvmadmin."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/kvmadmin/start"
        )

    async def kvmadmin_stop(self) -> None:
        """Stop kvmadmin."""
        await self._client._api_request_json(
            hdrs.METH_POST, "/extensions/kvmadmin/stop"
        )

    async def kvmadmin_status(self) -> GetKvmadminStatusRsp:
        """Get kvmadmin status."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/extensions/kvmadmin/status",
            response_model=GetKvmadminStatusRsp,
        )
