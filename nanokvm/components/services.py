"""Internal services operations for the NanoKVM session."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import math
from os import PathLike
from pathlib import Path
import re

import aiohttp
from aiohttp import hdrs

from ..compatibility import (
    _version_at_least,
    require_application_version,
    require_hardware,
)
from ..exceptions import (
    NanoKVMError,
    NanoKVMInvalidResponseError,
    NanoKVMNotAuthenticatedError,
)
from ..models.common import (
    GetPreviewRsp,
    GetTailscaleStatusRsp,
    GetVersionRsp,
    HWFamily,
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
from ..utils import _validate_sha256
from .session import Controller

_OFFLINE_UPDATE_PACKAGE_RE = re.compile(r"^nanokvm_[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$")
_OFFLINE_UPDATE_TIMEOUT_SECONDS = 15 * 60
# The device asks its update server while answering; the web UI waits 60 s.
_VERSION_CHECK_TIMEOUT_SECONDS = 60.0
# The device downloads and installs before answering; the web UI waits 15 min.
_UPDATE_TIMEOUT_SECONDS = 15 * 60.0
# A restarting server can drop the connection, time out, or answer with a page
# that is not the API's JSON.
_RESTARTING_ERRORS = (aiohttp.ClientError, TimeoutError, NanoKVMInvalidResponseError)


def _calculate_file_sha256(file_path: Path) -> str:
    """Calculate a file checksum without loading it into memory."""
    with file_path.open("rb") as file_obj:
        return hashlib.file_digest(file_obj, "sha256").hexdigest()


class ServiceController(Controller):
    """Implement services operations behind the public client facade."""

    @require_application_version(non_pro="2.1.6")
    async def get_tailscale_status(self) -> GetTailscaleStatusRsp:
        """Get Tailscale status."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/extensions/tailscale/status",
            response_model=GetTailscaleStatusRsp,
        )

    async def get_application_version(
        self, *, timeout: float = _VERSION_CHECK_TIMEOUT_SECONDS
    ) -> GetVersionRsp:
        """Get current and latest application versions.

        ``latest`` is empty when the device cannot reach its update server.
        """
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/application/version",
            response_model=GetVersionRsp,
            timeout=aiohttp.ClientTimeout(total=timeout),
        )

    @require_application_version(non_pro="2.2.5")
    async def get_preview_status(self) -> GetPreviewRsp:
        """Check if preview updates are enabled."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/application/preview",
            response_model=GetPreviewRsp,
        )

    @require_application_version(non_pro="2.2.5")
    async def set_preview_state(self, enable: bool) -> None:
        """Enable or disable preview updates."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/application/preview",
            data=SetPreviewReq(enable=enable),
        )

    async def update_application(
        self, *, timeout: float = _UPDATE_TIMEOUT_SECONDS
    ) -> None:
        """Install the latest application version.

        The device answers once the update is installed, then restarts its
        server, which ends the session. Non-Pro firmware before 2.5.1 restarts
        before its answer is sent, so it drops the connection instead.
        """
        drops_connection = await self._restarts_before_answering()
        try:
            await self._session.api_request_json(
                hdrs.METH_POST,
                "/application/update",
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
        except aiohttp.ServerDisconnectedError:
            if not drops_connection:
                raise
        except aiohttp.ClientResponseError as err:
            if not self._is_restart_gateway_error(err):
                raise
        self._session.forget_versions()

    async def install_application_update(
        self, *, timeout: float = 10 * 60, poll_interval: float = 2.0
    ) -> str:
        """Update the application if needed, and wait until the device runs it.

        Returns the application version the device runs afterwards. The
        restart ends the session, so the client logs in again with the
        credentials it keeps with ``keep_credentials=True``. ``timeout`` bounds
        the wait for the restart, in seconds.
        """
        for value in (timeout, poll_interval):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("timeout and poll_interval must be positive seconds")
        session = self._session
        if session._credentials is None:
            raise NanoKVMNotAuthenticatedError(
                "install_application_update needs a client created with "
                "keep_credentials=True that has authenticated"
            )

        versions = await self.get_application_version()
        if not versions.latest:
            raise NanoKVMError("The device could not reach its update server")
        if _version_at_least(versions.current, versions.latest):
            return versions.current

        await self.update_application()
        return await self._wait_for_new_application(
            versions.current, timeout, poll_interval
        )

    async def _wait_for_new_application(
        self, previous: str, timeout: float, poll_interval: float
    ) -> str:
        """Wait until the device answers with another application version."""
        session = self._session
        try:
            async with asyncio.timeout(timeout):
                while True:
                    await asyncio.sleep(poll_interval)
                    try:
                        await session.detect_versions()
                    except NanoKVMNotAuthenticatedError:
                        # The restarted server rejects the old session. Log in
                        # again; a server still starting is retried next time.
                        with contextlib.suppress(*_RESTARTING_ERRORS):
                            await session.reauthenticate()
                        continue
                    except _RESTARTING_ERRORS:
                        continue
                    version = session._application_version
                    if version is not None and version != previous:
                        return version
        except TimeoutError:
            raise TimeoutError(
                f"The device did not run a new application version within "
                f"{timeout:g} seconds"
            ) from None

    async def _restarts_before_answering(self) -> bool:
        """Whether an update restarts the server before its answer is sent.

        Before 2.5.1, the non-Pro update handler runs the restart itself, which
        stops the server while the answer is still buffered.
        """
        session = self._session
        if await session.application_version_at_least(non_pro="2.5.1"):
            return False
        return session.is_hardware_family(HWFamily.NON_PRO)

    def _is_restart_gateway_error(self, err: aiohttp.ClientResponseError) -> bool:
        """Whether a 502 came from the device restarting after an update.

        On non-Pro 2.5.1 and newer, the device can stop its old HTTP service
        before the proxy has forwarded the successful response. Its official
        UI treats that gateway response as successful too.
        """
        version = self._session._application_version
        return (
            err.status == 502
            and version is not None
            and _version_at_least(version, "2.5.1")
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.3.1")
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
        _validate_sha256(checksum or None)

        if checksum:
            actual_checksum = await asyncio.to_thread(
                _calculate_file_sha256, package_path
            )
            if actual_checksum != checksum.lower():
                raise ValueError("SHA-256 checksum does not match the update package")

        supports_remote_checksum = (
            self._session._application_version is None
            or _version_at_least(self._session._application_version, "2.5.1")
        )
        headers = (
            {"X-SHA256-Checksum": checksum}
            if checksum and supports_remote_checksum
            else {}
        )
        drops_connection = await self._restarts_before_answering()
        try:
            await self._session.upload_file(
                "/application/update/offline",
                package_path,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=_OFFLINE_UPDATE_TIMEOUT_SECONDS),
            )
        except aiohttp.ServerDisconnectedError:
            if not drops_connection:
                raise
        except aiohttp.ClientResponseError as err:
            if not self._is_restart_gateway_error(err):
                raise
        self._session.forget_versions()

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.1")
    async def get_update_server(self) -> GetUpdateServerRsp:
        """Get the custom application update-server configuration."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/application/update-server",
            response_model=GetUpdateServerRsp,
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.1")
    async def set_update_server(
        self,
        enabled: bool,
        url: str,
    ) -> GetUpdateServerRsp:
        """Set and return the custom application update-server configuration."""
        return await self._session.api_request_json(
            hdrs.METH_POST,
            "/application/update-server",
            response_model=GetUpdateServerRsp,
            data=SetUpdateServerReq(enabled=enabled, url=url),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.0")
    async def get_mcp_config(self) -> GetMCPConfigRsp:
        """Get MCP configuration and coordinated-control state."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/mcp/config",
            response_model=GetMCPConfigRsp,
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.0")
    async def set_mcp_enabled(self, enabled: bool) -> GetMCPConfigRsp:
        """Enable or disable MCP control."""
        return await self._session.api_request_json(
            hdrs.METH_POST,
            "/mcp/config",
            response_model=GetMCPConfigRsp,
            data=SetMCPConfigReq(enabled=enabled),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.0")
    async def regenerate_mcp_api_key(self) -> GetMCPConfigRsp:
        """Generate and return a new MCP API key."""
        return await self._session.api_request_json(
            hdrs.METH_POST,
            "/mcp/key/regenerate",
            response_model=GetMCPConfigRsp,
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.0")
    async def get_ai_control_status(self) -> AIControlStatusRsp:
        """Get the current coordinated-control owner and transition state."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/ai/control/status",
            response_model=AIControlStatusRsp,
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.0")
    async def set_ai_control_mode(self, mode: AIControlMode) -> SetAIControlModeRsp:
        """Select the owner of coordinated input control."""
        return await self._session.api_request_json(
            hdrs.METH_PUT,
            "/ai/control/mode",
            response_model=SetAIControlModeRsp,
            data=SetAIControlModeReq(mode=mode),
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_install(self) -> None:
        """Install Tailscale."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/install"
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_uninstall(self) -> None:
        """Uninstall Tailscale."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/uninstall"
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_up(self) -> None:
        """Bring Tailscale up."""
        await self._session.api_request_json(hdrs.METH_POST, "/extensions/tailscale/up")

    @require_application_version(non_pro="2.1.6")
    async def tailscale_down(self) -> None:
        """Bring Tailscale down."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/down"
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_login(self) -> LoginTailscaleRsp:
        """Log in to Tailscale."""
        return await self._session.api_request_json(
            hdrs.METH_POST,
            "/extensions/tailscale/login",
            response_model=LoginTailscaleRsp,
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_logout(self) -> None:
        """Log out of Tailscale."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/logout"
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_start(self) -> None:
        """Start Tailscale service."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/start"
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_stop(self) -> None:
        """Stop Tailscale service."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/stop"
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_restart(self) -> None:
        """Restart Tailscale service."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/tailscale/restart"
        )

    @require_hardware(HWFamily.PRO, min_version="1.1.4")
    async def assistant_install(self) -> None:
        """Install assistant dependencies."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/assistant/install"
        )

    @require_hardware(HWFamily.PRO, min_version="1.1.4")
    async def assistant_start(self) -> None:
        """Start assistant."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/assistant/start"
        )

    @require_hardware(HWFamily.PRO, min_version="1.1.5")
    async def kvmadmin_install(self) -> None:
        """Install kvmadmin."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/kvmadmin/install"
        )

    @require_hardware(HWFamily.PRO, min_version="1.1.5")
    async def kvmadmin_uninstall(self) -> None:
        """Uninstall kvmadmin."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/kvmadmin/uninstall"
        )

    @require_hardware(HWFamily.PRO, min_version="1.1.5")
    async def kvmadmin_start(self) -> None:
        """Start kvmadmin."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/kvmadmin/start"
        )

    @require_hardware(HWFamily.PRO, min_version="1.1.5")
    async def kvmadmin_stop(self) -> None:
        """Stop kvmadmin."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/extensions/kvmadmin/stop"
        )

    @require_hardware(HWFamily.PRO, min_version="1.1.5")
    async def kvmadmin_status(self) -> GetKvmadminStatusRsp:
        """Get kvmadmin status."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/extensions/kvmadmin/status",
            response_model=GetKvmadminStatusRsp,
        )
