"""API client for NanoKVM."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
import contextlib
import logging
from os import PathLike
import ssl
from typing import Any, Literal, TypeVar, overload

from aiohttp import (
    ClientResponse,
    ClientSession,
    ClientTimeout,
    ClientWebSocketResponse,
    Fingerprint,
    FormData,
)
from PIL import Image
from pydantic import BaseModel
import yarl

from .compatibility import (
    F,
    _version_at_least,
    require_application_version,
    require_hardware,
)
from .components.hid import PASTE_CHAR_MAP, HidController
from .components.mouse import MouseController
from .components.network import NetworkController
from .components.services import ServiceController
from .components.session import SessionController
from .components.storage import (
    ImageTransferProgressCallback,
    StorageController,
    _validate_sha256_argument,
)
from .components.stream import StreamController
from .components.system import SystemController
from .components.video import VideoController
from .exceptions import (
    NanoKVMApiError,
    NanoKVMAuthenticationFailure,
    NanoKVMError,
    NanoKVMInvalidResponseError,
    NanoKVMNotAuthenticatedError,
    NanoKVMNotSupportedError,
    NanoKVMPermissionError,
)
from .models.common import (
    AddShortcutReq,
    ApiResponseCode,
    ConnectWifiReq,
    DeleteImageReq,
    DeleteMacReq,
    DeleteScriptReq,
    DeleteShortcutReq,
    DownloadImageReq,
    DownloadStatus,
    GetAccountRsp,
    GetGpioRsp,
    GetHardwareRsp,
    GetHidModeRsp,
    GetHostnameRsp,
    GetImagesRsp,
    GetInfoRsp,
    GetKeyboardLedStatusRsp,
    GetLeaderKeyRsp,
    GetMacRsp,
    GetMdnsStateRsp,
    GetMountedImageRsp,
    GetMouseJigglerRsp,
    GetOLEDRsp,
    GetPreviewRsp,
    GetScriptsRsp,
    GetShortcutsRsp,
    GetSSHStateRsp,
    GetTailscaleStatusRsp,
    GetVersionRsp,
    GetVirtualDeviceRsp,
    GetWebTitleRsp,
    GetWifiRsp,
    GpioType,
    HidMode,
    HWFamily,
    HWVersion,
    ImageEnabledRsp,
    ImageTransferProgress,
    IsPasswordUpdatedRsp,
    LoginTailscaleRsp,
    MountImageReq,
    MouseButton,
    MouseJigglerMode,
    PasteReq,
    RunScriptReq,
    RunScriptRsp,
    RunScriptType,
    SetGpioReq,
    SetHidModeReq,
    SetHostnameReq,
    SetLeaderKeyReq,
    SetMacNameReq,
    SetMouseJigglerReq,
    SetOledReq,
    SetPreviewReq,
    SetWebTitleReq,
    ShortcutKey,
    StatusImageRsp,
    UpdateVirtualDeviceReq,
    UploadScriptRsp,
    VirtualDevice,
    WakeOnLANReq,
)
from .models.non_pro import (
    AIControlMode,
    AIControlStatusRsp,
    DNSMode,
    GetCdRomRsp,
    GetDNSRsp,
    GetHdmiStateRsp,
    GetInputRegionRsp,
    GetInputResolutionRsp,
    GetMCPConfigRsp,
    GetMemoryLimitRsp,
    GetSwapSizeRsp,
    GetUpdateServerRsp,
    InputRegionMode,
    ManualRegion,
    OriginalResolution,
    ScreenSettingType,
    SetAIControlModeReq,
    SetAIControlModeRsp,
    SetDNSReq,
    SetHdmiIdleTimeoutReq,
    SetInputRegionReq,
    SetMCPConfigReq,
    SetMemoryLimitReq,
    SetScreenReq,
    SetSwapSizeReq,
    SetUpdateServerReq,
)
from .models.pro import (
    DeleteEdidReq,
    DiskType,
    EdidValue,
    GetCustomEdidListRsp,
    GetEdidRsp,
    GetHdmiCaptureRsp,
    GetHdmiPassthroughRsp,
    GetKvmadminStatusRsp,
    GetLcdTimeFormatRsp,
    GetLedStripRsp,
    GetLowPowerRsp,
    GetMenuBarConfigRsp,
    GetStaticIPRsp,
    GetTimeStatusRsp,
    GetTimeZoneRsp,
    LcdTimeFormat,
    RateControlMode,
    RefreshVirtualDeviceReq,
    ScanWifiRsp,
    SetFpsReq,
    SetGopReq,
    SetHdmiCaptureReq,
    SetHdmiPassthroughReq,
    SetLcdTimeFormatReq,
    SetLedStripReq,
    SetLowPowerReq,
    SetMenuBarConfigReq,
    SetRateControlModeReq,
    SetStaticIPReq,
    SetStreamModeReq,
    SetStreamQualityReq,
    SetTimeZoneReq,
    StreamMode,
    SwitchEdidReq,
    UploadEdidRsp,
)
from .utils import obfuscate_password

__all__ = [
    "AIControlMode",
    "AIControlStatusRsp",
    "AddShortcutReq",
    "ApiResponseCode",
    "ConnectWifiReq",
    "DNSMode",
    "DeleteEdidReq",
    "DeleteImageReq",
    "DeleteMacReq",
    "DeleteScriptReq",
    "DeleteShortcutReq",
    "DiskType",
    "DownloadImageReq",
    "DownloadStatus",
    "EdidValue",
    "F",
    "GetAccountRsp",
    "GetCdRomRsp",
    "GetCustomEdidListRsp",
    "GetDNSRsp",
    "GetEdidRsp",
    "GetGpioRsp",
    "GetHardwareRsp",
    "GetHdmiCaptureRsp",
    "GetHdmiPassthroughRsp",
    "GetHdmiStateRsp",
    "GetHidModeRsp",
    "GetHostnameRsp",
    "GetImagesRsp",
    "GetInfoRsp",
    "GetInputRegionRsp",
    "GetInputResolutionRsp",
    "GetKeyboardLedStatusRsp",
    "GetKvmadminStatusRsp",
    "GetLcdTimeFormatRsp",
    "GetLeaderKeyRsp",
    "GetLedStripRsp",
    "GetLowPowerRsp",
    "GetMCPConfigRsp",
    "GetMacRsp",
    "GetMdnsStateRsp",
    "GetMemoryLimitRsp",
    "GetMenuBarConfigRsp",
    "GetMountedImageRsp",
    "GetMouseJigglerRsp",
    "GetOLEDRsp",
    "GetPreviewRsp",
    "GetSSHStateRsp",
    "GetScriptsRsp",
    "GetShortcutsRsp",
    "GetStaticIPRsp",
    "GetSwapSizeRsp",
    "GetTailscaleStatusRsp",
    "GetTimeStatusRsp",
    "GetTimeZoneRsp",
    "GetUpdateServerRsp",
    "GetVersionRsp",
    "GetVirtualDeviceRsp",
    "GetWebTitleRsp",
    "GetWifiRsp",
    "GpioType",
    "HWFamily",
    "HWVersion",
    "HidMode",
    "ImageEnabledRsp",
    "ImageTransferProgress",
    "ImageTransferProgressCallback",
    "InputRegionMode",
    "IsPasswordUpdatedRsp",
    "LcdTimeFormat",
    "LoginTailscaleRsp",
    "ManualRegion",
    "MountImageReq",
    "MouseButton",
    "MouseController",
    "MouseJigglerMode",
    "NanoKVMApiError",
    "NanoKVMAuthenticationFailure",
    "NanoKVMClient",
    "NanoKVMError",
    "NanoKVMInvalidResponseError",
    "NanoKVMNotAuthenticatedError",
    "NanoKVMNotSupportedError",
    "NanoKVMPermissionError",
    "OriginalResolution",
    "PASTE_CHAR_MAP",
    "PasteReq",
    "RateControlMode",
    "RefreshVirtualDeviceReq",
    "RunScriptReq",
    "RunScriptRsp",
    "RunScriptType",
    "ScanWifiRsp",
    "ScreenSettingType",
    "SessionController",
    "SetAIControlModeReq",
    "SetAIControlModeRsp",
    "SetDNSReq",
    "SetFpsReq",
    "SetGopReq",
    "SetGpioReq",
    "SetHdmiCaptureReq",
    "SetHdmiIdleTimeoutReq",
    "SetHdmiPassthroughReq",
    "SetHidModeReq",
    "SetHostnameReq",
    "SetInputRegionReq",
    "SetLcdTimeFormatReq",
    "SetLeaderKeyReq",
    "SetLedStripReq",
    "SetLowPowerReq",
    "SetMCPConfigReq",
    "SetMacNameReq",
    "SetMemoryLimitReq",
    "SetMenuBarConfigReq",
    "SetMouseJigglerReq",
    "SetOledReq",
    "SetPreviewReq",
    "SetRateControlModeReq",
    "SetScreenReq",
    "SetStaticIPReq",
    "SetStreamModeReq",
    "SetStreamQualityReq",
    "SetSwapSizeReq",
    "SetTimeZoneReq",
    "SetUpdateServerReq",
    "SetWebTitleReq",
    "ShortcutKey",
    "StatusImageRsp",
    "StreamController",
    "StreamMode",
    "SwitchEdidReq",
    "T",
    "UpdateVirtualDeviceReq",
    "UploadEdidRsp",
    "UploadScriptRsp",
    "VirtualDevice",
    "WakeOnLANReq",
    "obfuscate_password",
    "require_application_version",
    "require_hardware",
]

T = TypeVar("T", bound=BaseModel)

_LOGGER = logging.getLogger(__name__)


class NanoKVMClient:
    """Async API client for the NanoKVM."""

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        request_timeout: int = 10,
        session: ClientSession | None = None,
        verify_ssl: bool = True,
        ssl_ca_cert: str | None = None,
        ssl_fingerprint: str | None = None,
        use_password_obfuscation: bool | None = None,
    ) -> None:
        """
        Initialize the NanoKVM client.

        Args:
            url: Base URL of the NanoKVM API (e.g., "https://kvm.local/api/")
            session: aiohttp ClientSession to use for requests.
            token: Optional pre-existing authentication token
            request_timeout: Request timeout in seconds (default: 10)
            verify_ssl: Enable SSL certificate verification (default: True).
                Set to False to disable verification for self-signed certificates.
            ssl_ca_cert: Path to custom CA certificate bundle file for SSL verification.
                Useful for self-signed certificates or private CAs.
            ssl_fingerprint: SHA-256 fingerprint of the server's TLS certificate
                as a hex string. When set, the client will verify the server's
                certificate fingerprint instead of performing CA-based verification.
                Use `async_fetch_remote_fingerprint()` to retrieve this value.
            use_password_obfuscation: Control password obfuscation mode (default: None).
                None = auto-detect (try obfuscated first, fall back to plain text).
                True = always use obfuscated passwords (older NanoKVM versions).
                False = always use plain text passwords (newer HTTPS-enabled versions).
        """
        self.url = yarl.URL(url)
        self._session: ClientSession | None = session
        self._external_session_provided = session is not None
        self._token = token
        self._session_generation = 0
        self._request_timeout = request_timeout
        self._image_transfer_lock = asyncio.Lock()
        self._ws: ClientWebSocketResponse | None = None
        self._ws_session: ClientSession | None = None
        self._ws_lock = asyncio.Lock()
        self._mouse_buttons = 0
        self._mouse_mode: Literal["relative", "absolute"] = "relative"
        self._mouse_abs_position: tuple[int, int] = (0, 0)
        self._verify_ssl = verify_ssl
        self._ssl_ca_cert = ssl_ca_cert
        self._ssl_fingerprint = ssl_fingerprint
        self._use_password_obfuscation = use_password_obfuscation
        self._ssl_config: ssl.SSLContext | Fingerprint | bool | None = None
        self._hw_version: HWVersion | None = None
        self._application_version: str | None = None
        self._image_version: str | None = None
        self._session_controller = SessionController(self, _LOGGER)
        self._mouse = MouseController(self, _LOGGER)
        self._stream_controller = StreamController(self, _LOGGER)
        self._storage_controller = StorageController(self)
        self._services_controller = ServiceController(self)
        self._video_controller = VideoController(self)
        self._network_controller = NetworkController(self)
        self._system_controller = SystemController(self)
        self._hid_controller = HidController(self)

    def _create_ssl_context(self) -> ssl.SSLContext | Fingerprint | bool:
        """Create and configure the SSL context for the HTTP session."""
        return self._session_controller.create_ssl_context()

    @property
    def token(self) -> str | None:
        """Return the current auth token."""
        return self._token

    @property
    def hw_version(self) -> HWVersion | None:
        """The detected hardware version. None if not yet detected."""
        return self._hw_version

    @property
    def application_version(self) -> str | None:
        """The detected application version. None if not yet detected."""
        return self._application_version

    @property
    def image_version(self) -> str | None:
        """The detected image version. None if not yet detected."""
        return self._image_version

    async def detect_hardware(self) -> None:
        """Detect and store the hardware version."""
        hw = await self.get_hardware()
        self._hw_version = hw.version
        _LOGGER.info("Detected hardware: %s", hw.version)

    async def detect_versions(self) -> None:
        """Detect and store image and application versions."""
        info = await self.get_info()
        self._application_version = info.application
        self._image_version = info.image
        _LOGGER.info(
            "Detected versions: application=%s image=%s",
            info.application,
            info.image,
        )

    async def __aenter__(self) -> NanoKVMClient:
        """Async context manager entry."""
        await self._session_controller.enter()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - cleanup resources."""
        await self._session_controller.exit()

    @contextlib.asynccontextmanager
    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticate: bool = True,
        timeout: ClientTimeout | None = None,
        expected_generation: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ClientResponse, None]:
        """Make an API request."""
        async with self._session_controller.request(
            method,
            path,
            authenticate=authenticate,
            timeout=timeout,
            expected_generation=expected_generation,
            **kwargs,
        ) as response:
            yield response

    async def _read_json_response(self, response: ClientResponse) -> Any:
        """Read a JSON response and normalize decoding failures."""
        return await self._session_controller.read_json_response(response)

    @overload
    async def _api_request_json(
        self,
        method: str,
        path: str,
        response_model: type[T],
        data: BaseModel | None = None,
        **kwargs: Any,
    ) -> T: ...

    @overload
    async def _api_request_json(
        self,
        method: str,
        path: str,
        response_model: None = None,
        data: BaseModel | None = None,
        **kwargs: Any,
    ) -> None: ...

    async def _api_request_json(
        self,
        method: str,
        path: str,
        response_model: type[T] | None = None,
        data: BaseModel | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Make API request and parse JSON response."""
        return await self._session_controller.api_request_json(
            method,
            path,
            response_model=response_model,
            data=data,
            **kwargs,
        )

    @overload
    async def _api_request_form(
        self,
        method: str,
        path: str,
        response_model: type[T],
        data: FormData,
        **kwargs: Any,
    ) -> T: ...

    @overload
    async def _api_request_form(
        self,
        method: str,
        path: str,
        response_model: None = None,
        data: FormData | None = None,
        **kwargs: Any,
    ) -> None: ...

    async def _api_request_form(
        self,
        method: str,
        path: str,
        response_model: type[T] | None = None,
        data: FormData | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Make API request with multipart/form data and parse JSON response."""
        return await self._session_controller.api_request_form(
            method,
            path,
            response_model=response_model,
            data=data,
            **kwargs,
        )

    def _validate_api_response(
        self,
        raw_response: Any,
        response_model: type[T] | None = None,
    ) -> T | None:
        """Validate the shared NanoKVM response envelope."""
        return self._session_controller.validate_api_response(
            raw_response, response_model
        )

    @overload
    async def _upload_file(
        self,
        path: str,
        file_path: str | PathLike[str],
        response_model: type[T],
        **kwargs: Any,
    ) -> T: ...

    @overload
    async def _upload_file(
        self,
        path: str,
        file_path: str | PathLike[str],
        response_model: None = None,
        **kwargs: Any,
    ) -> None: ...

    async def _upload_file(
        self,
        path: str,
        file_path: str | PathLike[str],
        response_model: type[T] | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Upload a file using the NanoKVM multipart API."""
        return await self._session_controller.upload_file(
            path,
            file_path,
            response_model=response_model,
            **kwargs,
        )

    # ── Authentication ──────────────────────────────────────────────────

    async def _do_authenticate(
        self, username: str, password_to_send: str, *, generation: int
    ) -> None:
        """Perform a single authentication attempt with the given password."""
        await self._session_controller.do_authenticate(
            username, password_to_send, generation=generation
        )

    async def authenticate(self, username: str, password: str) -> None:
        """Authenticate and store the session token."""
        await self._session_controller.authenticate(username, password)

    def _check_session_generation(self, generation: int) -> None:
        """Reject an operation superseded by an authentication transition."""
        self._session_controller.check_session_generation(generation)

    async def logout(self) -> None:
        """Log out and clear the session token."""
        await self._session_controller.logout()

    async def _clear_local_session(
        self, *, expected_generation: int | None = None
    ) -> int:
        """Clear local authentication and transport state without closing HTTP."""
        return await self._session_controller.clear_local_session(
            expected_generation=expected_generation
        )

    def _clear_session_cookies(self) -> None:
        """Remove only session cookies whose scope overlaps this device's API."""
        self._session_controller.clear_session_cookies()

    def _is_hardware_family(self, family: HWFamily) -> bool:
        """Return whether the detected hardware belongs to ``family``."""
        return self._hw_version is not None and self._hw_version.family is family

    async def _ensure_image_transfer_version(self, minimum: str) -> None:
        """Check an image-transfer firmware minimum using cached device details."""
        if self._application_version is None and self._token is not None:
            await self.detect_versions()

        if self._application_version is not None and not _version_at_least(
            self._application_version, minimum
        ):
            family = "Pro" if self._is_hardware_family(HWFamily.PRO) else "non-Pro"
            raise NanoKVMNotSupportedError(
                f"image transfer requires {family} application version >= {minimum} "
                f"(detected: {self._application_version})"
            )

    async def _uses_current_password_contract(self) -> bool:
        """Determine whether this device uses the 2.5.1 password contract."""
        return await self._session_controller.uses_current_password_contract()

    async def change_password(
        self,
        username: str,
        new_password: str,
        *,
        current_password: str | None = None,
    ) -> None:
        """Change the KVM password for the authenticated account."""
        await self._session_controller.change_password(
            username,
            new_password,
            current_password=current_password,
        )

    async def is_password_updated(self) -> IsPasswordUpdatedRsp:
        """Check if the default password has been changed."""
        return await self._session_controller.is_password_updated()

    async def get_account(self) -> GetAccountRsp:
        """Get the configured username."""
        return await self._session_controller.get_account()

    # ── VM (shared) ─────────────────────────────────────────────────────

    async def get_info(self) -> GetInfoRsp:
        """Get general device information."""
        return await self._system_controller.get_info()

    async def get_hardware(self) -> GetHardwareRsp:
        """Get hardware version information."""
        return await self._system_controller.get_hardware()

    @require_application_version(non_pro="2.2.6")
    async def get_hostname(self) -> GetHostnameRsp:
        """Get the configured hostname."""
        return await self._system_controller.get_hostname()

    @require_application_version(non_pro="2.2.6")
    async def set_hostname(self, hostname: str) -> None:
        """Set the device hostname (applies after reboot)."""
        await self._system_controller.set_hostname(hostname)

    async def get_gpio(self) -> GetGpioRsp:
        """Get GPIO LED status."""
        return await self._system_controller.get_gpio()

    async def get_scripts(self) -> GetScriptsRsp:
        """Get the list of uploaded scripts."""
        return await self._storage_controller.get_scripts()

    async def upload_script(self, file_path: str | PathLike[str]) -> UploadScriptRsp:
        """Upload a script file."""
        return await self._storage_controller.upload_script(file_path)

    async def run_script(self, name: str, script_type: RunScriptType) -> RunScriptRsp:
        """Run an uploaded script."""
        return await self._storage_controller.run_script(name, script_type)

    async def delete_script(self, name: str) -> None:
        """Delete an uploaded script."""
        return await self._storage_controller.delete_script(name)

    async def push_button(self, button: GpioType, duration_ms: int) -> None:
        """Simulate pushing a hardware button."""
        await self._system_controller.push_button(button, duration_ms)

    @require_application_version(non_pro="2.1.6")
    async def get_ssh_state(self) -> GetSSHStateRsp:
        """Get SSH enabled state."""
        return await self._system_controller.get_ssh_state()

    @require_application_version(non_pro="2.1.6")
    async def enable_ssh(self) -> None:
        """Enable SSH server."""
        await self._system_controller.enable_ssh()

    @require_application_version(non_pro="2.1.6")
    async def disable_ssh(self) -> None:
        """Disable SSH server."""
        await self._system_controller.disable_ssh()

    @require_application_version(non_pro="2.2.2")
    async def get_mdns_state(self) -> GetMdnsStateRsp:
        """Get mDNS enabled state."""
        return await self._system_controller.get_mdns_state()

    @require_application_version(non_pro="2.2.2")
    async def enable_mdns(self) -> None:
        """Enable mDNS."""
        await self._system_controller.enable_mdns()

    @require_application_version(non_pro="2.2.2")
    async def disable_mdns(self) -> None:
        """Disable mDNS."""
        await self._system_controller.disable_mdns()

    async def get_oled_info(self) -> GetOLEDRsp:
        """Get OLED information."""
        return await self._system_controller.get_oled_info()

    async def set_oled_sleep(self, sleep_seconds: int) -> None:
        """Set the OLED sleep timeout."""
        await self._system_controller.set_oled_sleep(sleep_seconds)

    async def get_virtual_device_status(self) -> GetVirtualDeviceRsp:
        """Get the status of virtual devices."""
        return await self._system_controller.get_virtual_device_status()

    async def update_virtual_device(
        self,
        device: VirtualDevice,
        *,
        disk_type: DiskType | None = None,  # Pro only
    ) -> None:
        """Toggle the state of a virtual device."""
        await self._system_controller.update_virtual_device(device, disk_type=disk_type)

    @require_application_version(non_pro="2.2.6")
    async def get_mouse_jiggler_state(self) -> GetMouseJigglerRsp:
        """Get the mouse jiggler state."""
        return await self._hid_controller.get_mouse_jiggler_state()

    @require_application_version(non_pro="2.2.6")
    async def set_mouse_jiggler_state(
        self, enabled: bool, mode: MouseJigglerMode
    ) -> None:
        """Set the mouse jiggler state."""
        await self._hid_controller.set_mouse_jiggler_state(enabled, mode)

    @require_application_version(non_pro="2.2.6")
    async def get_web_title(self) -> GetWebTitleRsp:
        """Get the web page title."""
        return await self._system_controller.get_web_title()

    @require_application_version(non_pro="2.2.6")
    async def set_web_title(self, title: str) -> None:
        """Set the web page title."""
        await self._system_controller.set_web_title(title)

    @require_application_version(non_pro="2.2.2")
    async def reboot_system(self) -> None:
        """Reboot the KVM device."""
        await self._system_controller.reboot_system()

    @require_hardware(HWFamily.PRO)
    async def switch_to_pikvm(self) -> None:
        """Switch the system image to PiKVM."""
        await self._system_controller.switch_to_pikvm()

    # ── VM (non-Pro only) ──────────────────────────────────────────────

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def get_input_region(self) -> GetInputRegionRsp:
        """Get the configured non-Pro input region."""
        return await self._video_controller.get_input_region()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def set_input_region(
        self,
        mode: InputRegionMode,
        *,
        frame_width: int | None = None,
        frame_height: int | None = None,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        resolutions: list[OriginalResolution] | None = None,
        selected_resolution: str | None = None,
        regions: list[ManualRegion] | None = None,
        selected_region: str | None = None,
    ) -> None:
        """Set the non-Pro input region configuration."""
        await self._video_controller.set_input_region(
            mode,
            frame_width=frame_width,
            frame_height=frame_height,
            left=left,
            top=top,
            width=width,
            height=height,
            resolutions=resolutions,
            selected_resolution=selected_resolution,
            regions=regions,
            selected_region=selected_region,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def get_input_resolution(self) -> GetInputResolutionRsp:
        """Get the current non-Pro input frame resolution."""
        return await self._video_controller.get_input_resolution()

    @require_hardware(HWFamily.NON_PRO)
    async def set_screen(self, setting: ScreenSettingType, value: int) -> None:
        """Set a non-Pro NanoKVM screen setting."""
        await self._video_controller.set_screen(setting, value)

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.6")
    async def get_swap_size(self) -> int:
        """Get Swap size."""
        return await self._system_controller.get_swap_size()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.6")
    async def set_swap_size(self, size_mb: int) -> None:
        """Set the Swap size."""
        await self._system_controller.set_swap_size(size_mb)

    @require_hardware(HWFamily.NON_PRO)
    async def get_memory_limit(self) -> GetMemoryLimitRsp:
        """Get the configured Go memory limit."""
        return await self._system_controller.get_memory_limit()

    @require_hardware(HWFamily.NON_PRO)
    async def set_memory_limit(self, enabled: bool, limit_mb: int) -> None:
        """Set or disable the Go memory limit."""
        await self._system_controller.set_memory_limit(enabled, limit_mb)

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def get_hdmi_state(self) -> GetHdmiStateRsp:
        """Get the HDMI state (PCIe variant)."""
        return await self._video_controller.get_hdmi_state()

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.1.5")
    async def reset_hdmi(self) -> None:
        """Reset the HDMI connection."""
        await self._video_controller.reset_hdmi()

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def enable_hdmi(self) -> None:
        """Enable the HDMI connection."""
        await self._video_controller.enable_hdmi()

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def disable_hdmi(self) -> None:
        """Disable the HDMI connection."""
        await self._video_controller.disable_hdmi()

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.5.0")
    async def set_hdmi_idle_timeout(self, minutes: int) -> None:
        """Set the HDMI capture idle timeout in minutes; zero disables it."""
        await self._video_controller.set_hdmi_idle_timeout(minutes)

    # ── VM (Pro only) ──────────────────────────────────────────────────

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def refresh_virtual_device(self, device: str) -> None:
        """Refresh a virtual device (e.g. emmc)."""
        await self._system_controller.refresh_virtual_device(device)

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_lcd_time_format(self) -> GetLcdTimeFormatRsp:
        """Get the LCD time format."""
        return await self._system_controller.get_lcd_time_format()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def set_lcd_time_format(self, fmt: LcdTimeFormat | str) -> None:
        """Set the LCD time format (12h/24h)."""
        await self._system_controller.set_lcd_time_format(fmt)

    @require_hardware(HWFamily.PRO)
    async def get_hdmi_capture(self) -> GetHdmiCaptureRsp:
        """Get HDMI capture status."""
        return await self._video_controller.get_hdmi_capture()

    @require_hardware(HWFamily.PRO)
    async def set_hdmi_capture(self, enabled: bool) -> None:
        """Set HDMI capture status."""
        await self._video_controller.set_hdmi_capture(enabled)

    @require_hardware(HWFamily.PRO)
    async def get_hdmi_passthrough(self) -> GetHdmiPassthroughRsp:
        """Get HDMI passthrough status."""
        return await self._video_controller.get_hdmi_passthrough()

    @require_hardware(HWFamily.PRO)
    async def set_hdmi_passthrough(self, enabled: bool) -> None:
        """Set HDMI passthrough status."""
        await self._video_controller.set_hdmi_passthrough(enabled)

    @require_hardware(HWFamily.PRO)
    async def get_edid(self) -> GetEdidRsp:
        """Get current EDID."""
        return await self._video_controller.get_edid()

    @require_hardware(HWFamily.PRO)
    async def switch_edid(self, edid: EdidValue) -> None:
        """Switch EDID."""
        await self._video_controller.switch_edid(edid)

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def get_custom_edid_list(self) -> GetCustomEdidListRsp:
        """Get custom EDID list."""
        return await self._video_controller.get_custom_edid_list()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def upload_edid(self, file_path: str | PathLike[str]) -> UploadEdidRsp:
        """Upload a custom EDID."""
        return await self._video_controller.upload_edid(file_path)

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def delete_edid(self, edid: str) -> None:
        """Delete a custom EDID."""
        await self._video_controller.delete_edid(edid)

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def get_low_power(self) -> GetLowPowerRsp:
        """Get low power status."""
        return await self._system_controller.get_low_power()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def set_low_power(self, enable: bool) -> None:
        """Set low power mode."""
        await self._system_controller.set_low_power(enable)

    @require_hardware(HWFamily.PRO)
    async def get_led_strip(self) -> GetLedStripRsp:
        """Get LED strip configuration."""
        return await self._system_controller.get_led_strip()

    @require_hardware(HWFamily.PRO)
    async def set_led_strip(
        self,
        *,
        on: bool | None = None,
        horizontal_count: int | None = None,
        vertical_count: int | None = None,
        brightness: int | None = None,
    ) -> None:
        """Set LED strip configuration."""
        await self._system_controller.set_led_strip(
            on=on,
            horizontal_count=horizontal_count,
            vertical_count=vertical_count,
            brightness=brightness,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_timezone(self) -> GetTimeZoneRsp:
        """Get the configured timezone."""
        return await self._system_controller.get_timezone()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def set_timezone(self, timezone: str) -> None:
        """Set the timezone."""
        await self._system_controller.set_timezone(timezone)

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_time_status(self) -> GetTimeStatusRsp:
        """Get time synchronization status."""
        return await self._system_controller.get_time_status()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def sync_time(self) -> None:
        """Synchronize time."""
        await self._system_controller.sync_time()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.10")
    async def get_menubar_config(self) -> GetMenuBarConfigRsp:
        """Get menu bar configuration."""
        return await self._system_controller.get_menubar_config()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.10")
    async def set_menubar_config(self, disabled_items: list[str]) -> None:
        """Set menu bar configuration."""
        await self._system_controller.set_menubar_config(disabled_items)

    # ── HID ─────────────────────────────────────────────────────────────

    @require_application_version(non_pro="2.2.5")
    async def get_hid_mode(self) -> GetHidModeRsp:
        """Get the current HID mode."""
        return await self._hid_controller.get_hid_mode()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def get_keyboard_led_status(self) -> GetKeyboardLedStatusRsp:
        """Get host keyboard LED state on non-Pro firmware 2.5.0 and newer.

        When ``known`` is false, the host has not reported its LED state yet.
        """
        return await self._hid_controller.get_keyboard_led_status()

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def get_shortcuts(self) -> GetShortcutsRsp:
        """Get configured custom HID shortcuts."""
        return await self._hid_controller.get_shortcuts()

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def add_shortcut(self, keys: list[ShortcutKey]) -> None:
        """Add a custom HID shortcut."""
        await self._hid_controller.add_shortcut(keys)

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def delete_shortcut(self, shortcut_id: str) -> None:
        """Delete a custom HID shortcut."""
        await self._hid_controller.delete_shortcut(shortcut_id)

    @require_application_version(non_pro="2.3.4", pro="1.2.12")
    async def get_leader_key(self) -> GetLeaderKeyRsp:
        """Get the configured shortcut leader key."""
        return await self._hid_controller.get_leader_key()

    @require_application_version(non_pro="2.3.4", pro="1.2.12")
    async def set_leader_key(self, key: str = "") -> None:
        """Set or clear the shortcut leader key."""
        await self._hid_controller.set_leader_key(key)

    @require_application_version(non_pro="2.2.5")
    async def set_hid_mode(self, mode: HidMode) -> None:
        """Set the HID mode (requires reboot)."""
        await self._hid_controller.set_hid_mode(mode)

    @require_application_version(pro="1.1.6")
    async def reset_hid(self) -> None:
        """Reset the HID subsystem."""
        await self._hid_controller.reset_hid()

    async def paste_text(self, text: str) -> None:
        """Paste text via HID keyboard simulation."""
        await self._hid_controller.paste_text(text)

    # ── Storage ─────────────────────────────────────────────────────────

    async def get_images(self) -> GetImagesRsp:
        """Get the list of available image files."""
        return await self._storage_controller.get_images()

    async def upload_image(
        self,
        file_path: str | PathLike[str],
        *,
        progress_callback: ImageTransferProgressCallback | None = None,
        sha256: str | None = None,
        chunk_size: int = 1024 * 1024,
        overwrite: bool = False,
    ) -> None:
        """Upload an image, rejecting an existing name unless overwrite is enabled."""
        return await self._storage_controller.upload_image(
            file_path,
            progress_callback=progress_callback,
            sha256=sha256,
            chunk_size=chunk_size,
            overwrite=overwrite,
        )

    async def _upload_image(
        self,
        file_path: str | PathLike[str],
        *,
        progress_callback: ImageTransferProgressCallback | None = None,
        sha256: str | None = None,
        chunk_size: int = 1024 * 1024,
        overwrite: bool = False,
    ) -> None:
        """Upload a local image, streaming on non-Pro and chunking on Pro."""
        return await self._storage_controller._upload_image(
            file_path,
            progress_callback=progress_callback,
            sha256=sha256,
            chunk_size=chunk_size,
            overwrite=overwrite,
        )

    async def get_mounted_image(self) -> GetMountedImageRsp:
        """Get the currently mounted image file."""
        return await self._storage_controller.get_mounted_image()

    async def mount_image(
        self,
        file: str | None = None,
        cdrom: bool = False,
        *,
        read_only: bool = False,  # Pro only
    ) -> None:
        """Mount an image file or unmount if file is None."""
        return await self._storage_controller.mount_image(
            file, cdrom, read_only=read_only
        )

    @require_application_version(non_pro="2.3.0", pro="1.1.6")
    async def delete_image(self, file: str) -> None:
        """Delete an image file."""
        return await self._storage_controller.delete_image(file)

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.1")
    async def get_cdrom_status(self) -> GetCdRomRsp:
        """Check if the mounted image is in CD-ROM mode."""
        return await self._storage_controller.get_cdrom_status()

    # ── Network (shared) ───────────────────────────────────────────────

    async def get_wifi_status(self) -> GetWifiRsp:
        """Get WiFi status."""
        return await self._network_controller.get_wifi_status()

    @require_application_version(non_pro="2.3.1")
    async def connect_wifi(self, ssid: str, password: str) -> None:
        """Connect to a WiFi network."""
        await self._network_controller.connect_wifi(ssid, password)

    async def connect_wifi_no_auth(
        self,
        ssid: str,
        password: str = "",
        ap_password: str | None = None,
    ) -> None:
        """Connect to WiFi while the device is in AP-mode setup flow."""
        await self._network_controller.connect_wifi_no_auth(ssid, password, ap_password)

    @require_application_version(non_pro="2.3.6", pro="1.2.14")
    async def verify_ap_login(self, ap_password: str) -> None:
        """Verify AP-mode setup credentials."""
        await self._network_controller.verify_ap_login(ap_password)

    @require_application_version(non_pro="2.3.1")
    async def disconnect_wifi(self) -> None:
        """Disconnect from the current WiFi network."""
        await self._network_controller.disconnect_wifi()

    async def send_wake_on_lan(self, mac: str) -> None:
        """Send a Wake-on-LAN packet."""
        await self._network_controller.send_wake_on_lan(mac)

    async def get_wol_macs(self) -> GetMacRsp:
        """Get saved Wake-on-LAN MAC entries."""
        return await self._network_controller.get_wol_macs()

    async def delete_wol_mac(self, mac: str) -> None:
        """Delete a saved Wake-on-LAN MAC entry."""
        await self._network_controller.delete_wol_mac(mac)

    @require_application_version(non_pro="2.2.6")
    async def set_wol_mac_name(self, mac: str, name: str) -> None:
        """Set the display name for a saved Wake-on-LAN MAC entry."""
        await self._network_controller.set_wol_mac_name(mac, name)

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.4.1")
    async def get_dns(self) -> GetDNSRsp:
        """Get DNS configuration."""
        return await self._network_controller.get_dns()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.4.1")
    async def set_dns(
        self, mode: DNSMode | str, servers: list[str] | None = None
    ) -> None:
        """Set DNS configuration."""
        await self._network_controller.set_dns(mode, servers)

    @require_application_version(non_pro="2.1.6")
    async def get_tailscale_status(self) -> GetTailscaleStatusRsp:
        """Get Tailscale status."""
        return await self._services_controller.get_tailscale_status()

    # ── Network (Pro only) ─────────────────────────────────────────────

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def get_static_ip(self) -> GetStaticIPRsp:
        """Get static IP configuration."""
        return await self._network_controller.get_static_ip()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def set_static_ip(self, enabled: bool, ip: str) -> None:
        """Set static IP configuration."""
        await self._network_controller.set_static_ip(enabled, ip)

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.7")
    async def scan_wifi(self) -> ScanWifiRsp:
        """Scan for available WiFi networks."""
        return await self._network_controller.scan_wifi()

    # ── Stream (Pro only) ──────────────────────────────────────────────

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.6")
    async def set_rate_control_mode(self, mode: RateControlMode) -> None:
        """Set the stream rate control mode (CBR/VBR)."""
        await self._video_controller.set_rate_control_mode(mode)

    @require_hardware(HWFamily.PRO)
    async def set_stream_mode(self, mode: StreamMode | str) -> None:
        """Set the stream mode."""
        await self._video_controller.set_stream_mode(mode)

    @require_hardware(HWFamily.PRO)
    async def set_stream_quality(self, quality: int) -> None:
        """Set the stream quality / bit-rate."""
        await self._video_controller.set_stream_quality(quality)

    @require_hardware(HWFamily.PRO)
    async def set_gop(self, gop: int) -> None:
        """Set the stream GOP (Group of Pictures)."""
        await self._video_controller.set_gop(gop)

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.8")
    async def set_fps(self, fps: int) -> None:
        """Set the stream FPS."""
        await self._video_controller.set_fps(fps)

    # ── Stream (shared) ────────────────────────────────────────────────

    def _parse_jpeg_from_bytes(self, data: bytes) -> Image.Image:
        """Parse JPEG image from bytes."""
        return self._stream_controller.parse_jpeg_from_bytes(data)

    async def mjpeg_stream(self) -> AsyncIterator[Image.Image]:
        """Stream MJPEG frames."""
        async for image in self._stream_controller.mjpeg_stream():
            yield image

    # ── Application ─────────────────────────────────────────────────────

    async def get_application_version(self) -> GetVersionRsp:
        """Get current and latest application versions."""
        return await self._services_controller.get_application_version()

    @require_application_version(non_pro="2.2.5")
    async def get_preview_status(self) -> GetPreviewRsp:
        """Check if preview updates are enabled."""
        return await self._services_controller.get_preview_status()

    @require_application_version(non_pro="2.2.5")
    async def set_preview_state(self, enable: bool) -> None:
        """Enable or disable preview updates."""
        await self._services_controller.set_preview_state(enable)

    async def update_application(self) -> None:
        """Trigger the application update process."""
        await self._services_controller.update_application()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.3.1")
    async def update_application_offline(
        self,
        file_path: str | PathLike[str],
        *,
        sha256: str | None = None,
    ) -> None:
        """Install an official non-Pro application package from a local file."""
        await self._services_controller.update_application_offline(
            file_path, sha256=sha256
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def get_update_server(self) -> GetUpdateServerRsp:
        """Get the custom application update-server configuration."""
        return await self._services_controller.get_update_server()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def set_update_server(
        self,
        enabled: bool,
        url: str,
    ) -> GetUpdateServerRsp:
        """Set and return the custom application update-server configuration."""
        return await self._services_controller.set_update_server(enabled, url)

    # ── Download ────────────────────────────────────────────────────────

    def _image_download_prefix(self) -> str:
        """Return the image download route prefix for the detected hardware."""
        return self._storage_controller._image_download_prefix()

    @require_application_version(non_pro="2.1.6")
    async def is_image_download_enabled(self) -> ImageEnabledRsp:
        """Check if the /data partition allows downloads."""
        return await self._storage_controller.is_image_download_enabled()

    @require_application_version(non_pro="2.1.6")
    async def get_image_download_status(self) -> StatusImageRsp:
        """Get the status of an ongoing image download."""
        return await self._storage_controller.get_image_download_status()

    @require_application_version(non_pro="2.5.0")
    @require_hardware(HWFamily.NON_PRO)
    async def cancel_image_download(self) -> None:
        """Cancel an active non-Pro image download."""
        return await self._storage_controller.cancel_image_download()

    async def watch_image_download(
        self, *, poll_interval: float = 1.0
    ) -> AsyncIterator[StatusImageRsp]:
        """Poll image-download status until it reaches a terminal state."""
        source = self._storage_controller.watch_image_download(
            poll_interval=poll_interval
        )
        try:
            async for status in source:
                yield status
        finally:
            await source.aclose()

    @_validate_sha256_argument
    @require_application_version(non_pro="2.1.6")
    async def download_image(
        self, url: str, *, sha256: str | None = None
    ) -> StatusImageRsp:
        """Start downloading an image from a URL."""
        return await self._storage_controller.download_image(url, sha256=sha256)

    # ── MCP and coordinated control (non-Pro only) ─────────────────────

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def get_mcp_config(self) -> GetMCPConfigRsp:
        """Get MCP configuration and coordinated-control state."""
        return await self._services_controller.get_mcp_config()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def set_mcp_enabled(self, enabled: bool) -> GetMCPConfigRsp:
        """Enable or disable MCP control."""
        return await self._services_controller.set_mcp_enabled(enabled)

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def regenerate_mcp_api_key(self) -> GetMCPConfigRsp:
        """Generate and return a new MCP API key."""
        return await self._services_controller.regenerate_mcp_api_key()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def get_ai_control_status(self) -> AIControlStatusRsp:
        """Get the current coordinated-control owner and transition state."""
        return await self._services_controller.get_ai_control_status()

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def set_ai_control_mode(self, mode: AIControlMode) -> SetAIControlModeRsp:
        """Select the owner of coordinated input control."""
        return await self._services_controller.set_ai_control_mode(mode)

    # ── Extensions (shared) ────────────────────────────────────────────

    @require_application_version(non_pro="2.1.6")
    async def tailscale_install(self) -> None:
        """Install Tailscale."""
        await self._services_controller.tailscale_install()

    @require_application_version(non_pro="2.1.6")
    async def tailscale_uninstall(self) -> None:
        """Uninstall Tailscale."""
        await self._services_controller.tailscale_uninstall()

    @require_application_version(non_pro="2.1.6")
    async def tailscale_up(self) -> None:
        """Bring Tailscale up."""
        await self._services_controller.tailscale_up()

    @require_application_version(non_pro="2.1.6")
    async def tailscale_down(self) -> None:
        """Bring Tailscale down."""
        await self._services_controller.tailscale_down()

    @require_application_version(non_pro="2.1.6")
    async def tailscale_login(self) -> LoginTailscaleRsp:
        """Log in to Tailscale."""
        return await self._services_controller.tailscale_login()

    @require_application_version(non_pro="2.1.6")
    async def tailscale_logout(self) -> None:
        """Log out of Tailscale."""
        await self._services_controller.tailscale_logout()

    @require_application_version(non_pro="2.1.6")
    async def tailscale_start(self) -> None:
        """Start Tailscale service."""
        await self._services_controller.tailscale_start()

    @require_application_version(non_pro="2.1.6")
    async def tailscale_stop(self) -> None:
        """Stop Tailscale service."""
        await self._services_controller.tailscale_stop()

    @require_application_version(non_pro="2.1.6")
    async def tailscale_restart(self) -> None:
        """Restart Tailscale service."""
        await self._services_controller.tailscale_restart()

    # ── Extensions (Pro only) ──────────────────────────────────────────

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.4")
    async def assistant_install(self) -> None:
        """Install assistant dependencies."""
        await self._services_controller.assistant_install()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.4")
    async def assistant_start(self) -> None:
        """Start assistant."""
        await self._services_controller.assistant_start()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_install(self) -> None:
        """Install kvmadmin."""
        await self._services_controller.kvmadmin_install()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_uninstall(self) -> None:
        """Uninstall kvmadmin."""
        await self._services_controller.kvmadmin_uninstall()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_start(self) -> None:
        """Start kvmadmin."""
        await self._services_controller.kvmadmin_start()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_stop(self) -> None:
        """Stop kvmadmin."""
        await self._services_controller.kvmadmin_stop()

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_status(self) -> GetKvmadminStatusRsp:
        """Get kvmadmin status."""
        return await self._services_controller.kvmadmin_status()

    # ── Mouse (WebSocket) ──────────────────────────────────────────────

    async def _close_ws(self) -> None:
        """Close and forget the current WebSocket connection."""
        await self._mouse.close_ws()

    async def _invalidate_ws(self, ws: ClientWebSocketResponse) -> None:
        """Forget a failed WebSocket without closing a replacement connection."""
        await self._mouse.invalidate_ws(ws)

    async def _get_ws(self) -> ClientWebSocketResponse:
        """Get or create WebSocket connection for mouse events."""
        return await self._mouse.get_ws()

    async def _uses_binary_mouse_protocol(self) -> bool:
        """Select the mouse wire format supported by the connected device."""
        return await self._mouse.uses_binary_mouse_protocol()

    @staticmethod
    def _clamp(value: int, minimum: int, maximum: int) -> int:
        return MouseController.clamp(value, minimum, maximum)

    @classmethod
    def _relative_value(cls, value: float) -> int:
        return MouseController.relative_value(value)

    @classmethod
    def _absolute_value(cls, value: float) -> int:
        return MouseController.absolute_value(value)

    def _absolute_report(self, wheel: int = 0) -> bytes:
        return self._mouse.absolute_report(wheel)

    def _relative_report(self, dx: int = 0, dy: int = 0, wheel: int = 0) -> bytes:
        return self._mouse.relative_report(dx, dy, wheel)

    def _report_for_current_mode(self, *, wheel: int = 0) -> bytes:
        """Build a button or wheel report for the active mouse mode."""
        return self._mouse.report_for_current_mode(wheel=wheel)

    async def _send_ws(
        self,
        send: Callable[[ClientWebSocketResponse], Awaitable[None]],
    ) -> None:
        """Send one mouse message and invalidate the connection on failure."""
        await self._mouse.send_ws(send)

    async def _send_mouse_report(self, report: bytes) -> None:
        """Send a binary NanoKVM mouse event and HID report."""
        await self._mouse.send_mouse_report(report)

    async def _send_legacy_mouse_event(
        self, event_type: int, button_state: int, x: float, y: float
    ) -> None:
        """Send a mouse event using the pre-2.3.2 JSON wire format."""
        await self._mouse.send_legacy_mouse_event(event_type, button_state, x, y)

    async def mouse_move_abs(self, x: float, y: float) -> None:
        """
        Move mouse to absolute position.

        Args:
            x: X coordinate (0.0 to 1.0, left to right)
            y: Y coordinate (0.0 to 1.0, top to bottom)
        """
        await self._mouse.mouse_move_abs(x, y)

    async def mouse_move_rel(self, dx: float, dy: float) -> None:
        """
        Move mouse relative to current position.

        Args:
            dx: Horizontal movement (-1.0 to 1.0)
            dy: Vertical movement (-1.0 to 1.0)
        """
        await self._mouse.mouse_move_rel(dx, dy)

    async def mouse_down(self, button: MouseButton = MouseButton.LEFT) -> None:
        """
        Press a mouse button.

        Args:
            button: Mouse button to press (MouseButton.LEFT, MouseButton.RIGHT,
                MouseButton.MIDDLE, MouseButton.BACK, MouseButton.FORWARD)
        """
        await self._mouse.mouse_down(button)

    async def mouse_up(self) -> None:
        """
        Release a mouse button.

        The report releases all currently held buttons.
        """
        await self._mouse.mouse_up()

    async def mouse_click(
        self,
        button: MouseButton = MouseButton.LEFT,
        x: float | None = None,
        y: float | None = None,
    ) -> None:
        """
        Click a mouse button at current position or specified coordinates.

        Args:
            button: Mouse button to click (MouseButton.LEFT, MouseButton.RIGHT,
                MouseButton.MIDDLE, MouseButton.BACK, MouseButton.FORWARD)
            x: Optional X coordinate (0.0 to 1.0) for absolute positioning
                before click
            y: Optional Y coordinate (0.0 to 1.0) for absolute positioning
                before click
        """
        await self._mouse.mouse_click(button, x, y)

    async def mouse_scroll(self, dx: float, dy: float) -> None:
        """
        Scroll the mouse wheel.

        Args:
            dx: Horizontal scroll amount (-1.0 to 1.0)
            dy: Vertical scroll amount (-1.0 to 1.0) # positive=up, negative=down)
        """
        await self._mouse.mouse_scroll(dx, dy)
