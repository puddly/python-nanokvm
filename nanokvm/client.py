"""API client for NanoKVM."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
import contextlib
import functools
import io
import json
import logging
import ssl
from typing import Any, TypeVar, overload

import aiohttp
from aiohttp import (
    BodyPartReader,
    ClientResponse,
    ClientSession,
    Fingerprint,
    MultipartReader,
    hdrs,
)
from PIL import Image
from pydantic import BaseModel, ValidationError
import yarl

from .models.common import (
    ApiResponse,
    ApiResponseCode,
    ChangePasswordReq,
    ConnectWifiReq,
    DeleteImageReq,
    DownloadImageReq,
    GetAccountRsp,
    GetGpioRsp,
    GetHardwareRsp,
    GetHidModeRsp,
    GetHostnameRsp,
    GetImagesRsp,
    GetInfoRsp,
    GetMdnsStateRsp,
    GetMountedImageRsp,
    GetMouseJigglerRsp,
    GetOLEDRsp,
    GetPreviewRsp,
    GetSSHStateRsp,
    GetTailscaleStatusRsp,
    GetVersionRsp,
    GetWebTitleRsp,
    GetWifiRsp,
    GpioType,
    HidMode,
    HWVersion,
    ImageEnabledRsp,
    IsPasswordUpdatedRsp,
    LoginReq,
    LoginRsp,
    MountImageReq,
    MouseButton,
    MouseJigglerMode,
    PasteReq,
    SetGpioReq,
    SetHidModeReq,
    SetHostnameReq,
    SetMouseJigglerReq,
    SetOledReq,
    SetPreviewReq,
    SetWebTitleReq,
    StatusImageRsp,
    UpdateVirtualDeviceReq,
    VirtualDevice,
    WakeOnLANReq,
)
from .models.non_pro import (
    GetCdRomRsp,
    GetHdmiStateRsp,
    GetMemoryLimitRsp,
    GetSwapSizeRsp,
    GetVirtualDeviceRsp,
    SetMemoryLimitReq,
    SetSwapSizeReq,
)
from .models.pro import (
    DeleteEdidReq,
    DiskType,
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
    SwitchEdidReq,
)
from .utils import obfuscate_password

T = TypeVar("T")

_LOGGER = logging.getLogger(__name__)

PASTE_CHAR_MAP = set(
    "\t\n !\"#$%&'()*+,-./0123456789"
    ":;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
)


class NanoKVMError(Exception):
    """Base exception for NanoKVM client errors."""


class NanoKVMNotAuthenticatedError(NanoKVMError):
    """Exception for authentication errors."""


class NanoKVMApiError(NanoKVMError):
    """Exception for API-level errors reported by the device."""

    def __init__(self, message: str, code: int, msg: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.msg = msg
        self.data = data


class NanoKVMAuthenticationFailure(NanoKVMError):
    """Exception for authentication failure."""


class NanoKVMInvalidResponseError(NanoKVMError):
    """Exception for unexpected or unparsable responses."""


class NanoKVMNotSupportedError(NanoKVMError):
    """Feature not supported on this hardware variant."""


F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


def require_hardware(*versions: HWVersion) -> Callable[[F], F]:
    """Decorator that restricts a method to specific hardware versions."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(self: NanoKVMClient, *args: Any, **kwargs: Any) -> Any:
            if self._hw_version is None:
                raise NanoKVMError(
                    f"{func.__name__} requires hardware detection; "
                    f"call detect_hardware() first"
                )
            if self._hw_version not in versions:
                allowed = ", ".join(v.value for v in versions)
                raise NanoKVMNotSupportedError(
                    f"{func.__name__} requires hardware: {allowed} "
                    f"(detected: {self._hw_version})"
                )
            return await func(self, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


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
        self._request_timeout = request_timeout
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._verify_ssl = verify_ssl
        self._ssl_ca_cert = ssl_ca_cert
        self._ssl_fingerprint = ssl_fingerprint
        self._use_password_obfuscation = use_password_obfuscation
        self._ssl_config: ssl.SSLContext | Fingerprint | bool | None = None
        self._hw_version: HWVersion | None = None

    def _create_ssl_context(self) -> ssl.SSLContext | Fingerprint | bool:
        """
        Create and configure SSL context based on initialization parameters.

        Returns:
            Fingerprint: Certificate fingerprint pinning (when ssl_fingerprint set)
            ssl.SSLContext: Configured SSL context for custom certificates
            True: Use default SSL verification (aiohttp default)
            False: Disable SSL verification

        Raises:
            FileNotFoundError: If the CA certificate file is missing.
            ssl.SSLError: If the CA certificate is invalid.
        """

        if self._ssl_fingerprint:
            _LOGGER.debug("Using certificate fingerprint pinning")
            return Fingerprint(bytes.fromhex(self._ssl_fingerprint.replace(":", "")))

        if not self._verify_ssl:
            _LOGGER.warning(
                "SSL verification is disabled. This is insecure and should only be "
                "used for testing with self-signed certificates."
            )
            return False

        if not self._ssl_ca_cert:
            return True

        ssl_ctx = ssl.create_default_context(cafile=self._ssl_ca_cert)
        _LOGGER.debug("Using custom CA certificate: %s", self._ssl_ca_cert)

        return ssl_ctx

    @property
    def token(self) -> str | None:
        """Return the current auth token."""
        return self._token

    @property
    def hw_version(self) -> HWVersion | None:
        """The detected hardware version. None if not yet detected."""
        return self._hw_version

    async def detect_hardware(self) -> None:
        """Detect and store the hardware version."""
        hw = await self.get_hardware()
        self._hw_version = hw.version
        _LOGGER.info("Detected hardware: %s", hw.version)

    async def __aenter__(self) -> NanoKVMClient:
        """Async context manager entry."""
        if self._session is None and not self._external_session_provided:
            self._session = ClientSession()

        self._ssl_config = await asyncio.to_thread(self._create_ssl_context)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - cleanup resources."""
        # Close WebSocket connection
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
            self._ws = None

        # Close HTTP session
        if self._session is not None and not self._external_session_provided:
            await self._session.close()
            self._session = None

    @contextlib.asynccontextmanager
    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticate: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[ClientResponse]:
        """Make an API request."""
        cookies = {}
        if authenticate:
            if not self._token:
                raise NanoKVMNotAuthenticatedError("Client is not authenticated")
            cookies["nano-kvm-token"] = self._token

        assert self._session is not None
        assert self._ssl_config is not None

        async with self._session.request(
            method,
            self.url / path.lstrip("/"),
            headers={
                hdrs.ACCEPT: "application/json",
            },
            cookies=cookies,
            timeout=aiohttp.ClientTimeout(total=self._request_timeout),
            raise_for_status=True,
            ssl=self._ssl_config,
            **kwargs,
        ) as response:
            yield response

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
        _LOGGER.debug("Making API request: %s %s (%s)", method, path, data)

        async with self._request(
            method,
            path,
            json=(
                data.model_dump(by_alias=True, exclude_none=True)
                if data is not None
                else None
            ),
            **kwargs,
        ) as response:
            try:
                raw_response = await response.json(content_type=None)
                _LOGGER.debug("Raw JSON response data: %s", raw_response)
                # Parse the outer ApiResponse structure
                api_response = ApiResponse[response_model].model_validate(raw_response)  # type: ignore
            except (json.JSONDecodeError, ValidationError) as err:
                raise NanoKVMInvalidResponseError(
                    f"Invalid JSON response received: {err}"
                ) from err

        _LOGGER.debug("Got API response: %s", api_response)

        if api_response.code != ApiResponseCode.SUCCESS:
            raise NanoKVMApiError(
                f"API returned error: {api_response.msg} (Code: {api_response.code})",
                code=api_response.code,
                msg=api_response.msg,
                data=api_response.data,
            )

        return api_response.data

    # ── Authentication ──────────────────────────────────────────────────

    async def _do_authenticate(self, username: str, password_to_send: str) -> None:
        """Perform a single authentication attempt with the given password."""
        try:
            login_response = await self._api_request_json(
                hdrs.METH_POST,
                "/auth/login",
                response_model=LoginRsp,
                authenticate=False,
                data=LoginReq(
                    username=username,
                    password=password_to_send,
                ),
            )

            if not login_response.token:
                raise NanoKVMInvalidResponseError(
                    "Authentication response missing token."
                )

            self._token = login_response.token
        except NanoKVMApiError as err:
            if err.code == ApiResponseCode.INVALID_USERNAME_OR_PASSWORD:
                raise NanoKVMAuthenticationFailure(
                    "Invalid username or password"
                ) from err
            else:
                raise

    async def authenticate(self, username: str, password: str) -> None:
        """Authenticate and store the session token."""
        _LOGGER.debug("Attempting authentication for user: %s", username)

        if self._use_password_obfuscation is True:
            _LOGGER.debug("Using password obfuscation (forced)")
            await self._do_authenticate(username, obfuscate_password(password))
        elif self._use_password_obfuscation is False:
            _LOGGER.debug("Using plain text password (forced)")
            await self._do_authenticate(username, password)
        else:
            # Auto-detect: try obfuscated first, fall back to plain text
            _LOGGER.debug("Auto-detecting password mode")
            try:
                await self._do_authenticate(username, obfuscate_password(password))
                self._use_password_obfuscation = True
                _LOGGER.info("Auto-detected obfuscated password mode")
            except NanoKVMAuthenticationFailure:
                _LOGGER.debug(
                    "Obfuscated authentication failed, trying plain text password"
                )
                await self._do_authenticate(username, password)
                self._use_password_obfuscation = False
                _LOGGER.info("Auto-detected plain text password mode")

        await self.detect_hardware()

    async def logout(self) -> None:
        """Log out and clear the session token."""
        if not self._token or self._token == "disabled":
            return

        try:
            await self._api_request_json(hdrs.METH_POST, "/auth/logout")
        finally:
            self._token = None

    async def change_password(self, username: str, new_password: str) -> None:
        """Change the KVM password."""
        if self._use_password_obfuscation is None:
            raise ValueError(
                "Password mode is unknown. Authenticate first or set "
                "use_password_obfuscation explicitly before changing the password."
            )

        password_to_send = (
            obfuscate_password(new_password)
            if self._use_password_obfuscation
            else new_password
        )

        await self._api_request_json(
            hdrs.METH_POST,
            "/auth/password",
            data=ChangePasswordReq(
                username=username,
                password=password_to_send,
            ),
        )

    async def is_password_updated(self) -> IsPasswordUpdatedRsp:
        """Check if the default password has been changed."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/auth/password",
            response_model=IsPasswordUpdatedRsp,
        )

    async def get_account(self) -> GetAccountRsp:
        """Get the configured username."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/auth/account",
            response_model=GetAccountRsp,
        )

    # ── VM (shared) ─────────────────────────────────────────────────────

    async def get_info(self) -> GetInfoRsp:
        """Get general device information."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/info",
            response_model=GetInfoRsp,
        )

    async def get_hardware(self) -> GetHardwareRsp:
        """Get hardware version information."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hardware",
            response_model=GetHardwareRsp,
        )

    async def get_hostname(self) -> GetHostnameRsp:
        """Get the configured hostname."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hostname",
            response_model=GetHostnameRsp,
        )

    async def set_hostname(self, hostname: str) -> None:
        """Set the device hostname (applies after reboot)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/hostname",
            data=SetHostnameReq(hostname=hostname),
        )

    async def get_gpio(self) -> GetGpioRsp:
        """Get GPIO LED status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/gpio",
            response_model=GetGpioRsp,
        )

    async def push_button(self, button: GpioType, duration_ms: int) -> None:
        """Simulate pushing a hardware button."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/gpio",
            data=SetGpioReq(type=button, duration=duration_ms),
        )

    async def get_ssh_state(self) -> GetSSHStateRsp:
        """Get SSH enabled state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/ssh",
            response_model=GetSSHStateRsp,
        )

    async def enable_ssh(self) -> None:
        """Enable SSH server."""
        await self._api_request_json(hdrs.METH_POST, "/vm/ssh/enable")

    async def disable_ssh(self) -> None:
        """Disable SSH server."""
        await self._api_request_json(hdrs.METH_POST, "/vm/ssh/disable")

    async def get_mdns_state(self) -> GetMdnsStateRsp:
        """Get mDNS enabled state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/mdns",
            response_model=GetMdnsStateRsp,
        )

    async def enable_mdns(self) -> None:
        """Enable mDNS."""
        await self._api_request_json(hdrs.METH_POST, "/vm/mdns/enable")

    async def disable_mdns(self) -> None:
        """Disable mDNS."""
        await self._api_request_json(hdrs.METH_POST, "/vm/mdns/disable")

    async def get_oled_info(self) -> GetOLEDRsp:
        """Get OLED information."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/oled",
            response_model=GetOLEDRsp,
        )

    async def set_oled_sleep(self, sleep_seconds: int) -> None:
        """Set the OLED sleep timeout."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/oled",
            data=SetOledReq(sleep=sleep_seconds),
        )

    async def get_virtual_device_status(self) -> GetVirtualDeviceRsp:
        """Get the status of virtual devices."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/device/virtual",
            response_model=GetVirtualDeviceRsp,
        )

    async def update_virtual_device(
        self,
        device: VirtualDevice,
        *,
        disk_type: DiskType | None = None,  # Pro only
    ) -> None:
        """Toggle the state of a virtual device."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/device/virtual",
            data=UpdateVirtualDeviceReq(
                device=device,
                type=disk_type.value if disk_type else None,
            ),
        )

    async def get_mouse_jiggler_state(self) -> GetMouseJigglerRsp:
        """Get the mouse jiggler state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/mouse-jiggler",
            response_model=GetMouseJigglerRsp,
        )

    async def set_mouse_jiggler_state(
        self, enabled: bool, mode: MouseJigglerMode
    ) -> None:
        """Set the mouse jiggler state."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/mouse-jiggler/",
            data=SetMouseJigglerReq(enabled=enabled, mode=mode),
        )

    async def get_web_title(self) -> GetWebTitleRsp:
        """Get the web page title."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/web-title",
            response_model=GetWebTitleRsp,
        )

    async def set_web_title(self, title: str) -> None:
        """Set the web page title."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/web-title",
            data=SetWebTitleReq(title=title),
        )

    async def reboot_system(self) -> None:
        """Reboot the KVM device."""
        await self._api_request_json(hdrs.METH_POST, "/vm/system/reboot")

    # ── VM (non-Pro only) ──────────────────────────────────────────────

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def get_swap_size(self) -> int:
        """Get Swap size."""
        rsp = await self._api_request_json(
            hdrs.METH_GET,
            "/vm/swap",
            response_model=GetSwapSizeRsp,
        )
        return rsp.size

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def set_swap_size(self, size_mb: int) -> None:
        """Set the Swap size."""
        await self._api_request_json(
            hdrs.METH_POST, "/vm/swap", data=SetSwapSizeReq(size=size_mb)
        )

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def enable_swap(self) -> None:
        """Enable swap."""
        await self._api_request_json(hdrs.METH_POST, "/vm/swap/enable")

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def disable_swap(self) -> None:
        """Disable swap."""
        await self._api_request_json(hdrs.METH_POST, "/vm/swap/disable")

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def get_memory_limit(self) -> GetMemoryLimitRsp:
        """Get the configured Go memory limit."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/memory/limit",
            response_model=GetMemoryLimitRsp,
        )

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def set_memory_limit(self, enabled: bool, limit_mb: int) -> None:
        """Set or disable the Go memory limit."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/memory/limit",
            data=SetMemoryLimitReq(enabled=enabled, limit=limit_mb),
        )

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def get_hdmi_state(self) -> GetHdmiStateRsp:
        """Get the HDMI state (PCIe variant)."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi",
            response_model=GetHdmiStateRsp,
        )

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def reset_hdmi(self) -> None:
        """Reset the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/reset")

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def enable_hdmi(self) -> None:
        """Enable the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/enable")

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def disable_hdmi(self) -> None:
        """Disable the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/disable")

    # ── VM (Pro only) ──────────────────────────────────────────────────

    @require_hardware(HWVersion.PRO)
    async def refresh_virtual_device(self, device: str) -> None:
        """Refresh a virtual device (e.g. emmc)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/device/virtual/refresh",
            data=RefreshVirtualDeviceReq(device=device),
        )

    @require_hardware(HWVersion.PRO)
    async def get_lcd_time_format(self) -> GetLcdTimeFormatRsp:
        """Get the LCD time format."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/lcd/time/format",
            response_model=GetLcdTimeFormatRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def set_lcd_time_format(self, fmt: str) -> None:
        """Set the LCD time format (12h/24h)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/lcd/time/format",
            data=SetLcdTimeFormatReq(format=fmt),
        )

    @require_hardware(HWVersion.PRO)
    async def get_hdmi_capture(self) -> GetHdmiCaptureRsp:
        """Get HDMI capture status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi/capture",
            response_model=GetHdmiCaptureRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def set_hdmi_capture(self, enabled: bool) -> None:
        """Set HDMI capture status."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/capture",
            data=SetHdmiCaptureReq(enabled=enabled),
        )

    @require_hardware(HWVersion.PRO)
    async def get_hdmi_passthrough(self) -> GetHdmiPassthroughRsp:
        """Get HDMI passthrough status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi/passthrough",
            response_model=GetHdmiPassthroughRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def set_hdmi_passthrough(self, enabled: bool) -> None:
        """Set HDMI passthrough status."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/passthrough",
            data=SetHdmiPassthroughReq(enabled=enabled),
        )

    @require_hardware(HWVersion.PRO)
    async def get_edid(self) -> GetEdidRsp:
        """Get current EDID."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/edid",
            response_model=GetEdidRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def switch_edid(self, edid: str) -> None:
        """Switch EDID."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/edid",
            data=SwitchEdidReq(edid=edid),
        )

    @require_hardware(HWVersion.PRO)
    async def get_custom_edid_list(self) -> GetCustomEdidListRsp:
        """Get custom EDID list."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/edid/custom",
            response_model=GetCustomEdidListRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def delete_edid(self, edid: str) -> None:
        """Delete a custom EDID."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/edid/delete",
            data=DeleteEdidReq(edid=edid),
        )

    @require_hardware(HWVersion.PRO)
    async def get_low_power(self) -> GetLowPowerRsp:
        """Get low power status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/low-power",
            response_model=GetLowPowerRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def set_low_power(self, enable: bool) -> None:
        """Set low power mode."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/low-power",
            data=SetLowPowerReq(enable=enable),
        )

    @require_hardware(HWVersion.PRO)
    async def get_led_strip(self) -> GetLedStripRsp:
        """Get LED strip configuration."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/ledstrip/get",
            response_model=GetLedStripRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def set_led_strip(
        self,
        *,
        on: bool,
        horizontal_count: int,
        vertical_count: int,
        brightness: int,
    ) -> None:
        """Set LED strip configuration."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/ledstrip/set",
            data=SetLedStripReq(
                on=on,
                horizontal_count=horizontal_count,
                vertical_count=vertical_count,
                brightness=brightness,
            ),
        )

    @require_hardware(HWVersion.PRO)
    async def get_timezone(self) -> GetTimeZoneRsp:
        """Get the configured timezone."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/timezone",
            response_model=GetTimeZoneRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def set_timezone(self, timezone: str) -> None:
        """Set the timezone."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/timezone",
            data=SetTimeZoneReq(timezone=timezone),
        )

    @require_hardware(HWVersion.PRO)
    async def get_time_status(self) -> GetTimeStatusRsp:
        """Get time synchronization status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/time/status",
            response_model=GetTimeStatusRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def sync_time(self) -> None:
        """Synchronize time."""
        await self._api_request_json(hdrs.METH_POST, "/vm/time/sync")

    @require_hardware(HWVersion.PRO)
    async def get_menubar_config(self) -> GetMenuBarConfigRsp:
        """Get menu bar configuration."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/menubar",
            response_model=GetMenuBarConfigRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def set_menubar_config(self, disabled_items: list[str]) -> None:
        """Set menu bar configuration."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/menubar",
            data=SetMenuBarConfigReq(disabled_items=disabled_items),
        )

    # ── HID ─────────────────────────────────────────────────────────────

    async def get_hid_mode(self) -> GetHidModeRsp:
        """Get the current HID mode."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/hid/mode",
            response_model=GetHidModeRsp,
        )

    async def set_hid_mode(self, mode: HidMode) -> None:
        """Set the HID mode (requires reboot)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/hid/mode",
            data=SetHidModeReq(mode=mode),
        )

    async def reset_hid(self) -> None:
        """Reset the HID subsystem."""
        await self._api_request_json(hdrs.METH_POST, "/hid/reset")

    async def paste_text(self, text: str) -> None:
        """Paste text via HID keyboard simulation."""
        invalid_chars = set(text) - PASTE_CHAR_MAP
        if invalid_chars:
            raise ValueError(f"Invalid characters for paste: {invalid_chars}")
        await self._api_request_json(
            hdrs.METH_POST,
            "/hid/paste",
            data=PasteReq(content=text),
        )

    # ── Storage ─────────────────────────────────────────────────────────

    async def get_images(self) -> GetImagesRsp:
        """Get the list of available image files."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/storage/image",
            response_model=GetImagesRsp,
        )

    async def get_mounted_image(self) -> GetMountedImageRsp:
        """Get the currently mounted image file."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/storage/image/mounted",
            response_model=GetMountedImageRsp,
        )

    async def mount_image(
        self,
        file: str | None = None,
        cdrom: bool = False,
        *,
        read_only: bool = False,  # Pro only
    ) -> None:
        """Mount an image file or unmount if file is None."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/storage/image/mount",
            data=MountImageReq(
                file=file,
                cdrom=cdrom if file else None,
                read_only=read_only if file else None,
            ),
        )

    async def delete_image(self, file: str) -> None:
        """Delete an image file."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/storage/image/delete",
            data=DeleteImageReq(file=file),
        )

    @require_hardware(HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE)
    async def get_cdrom_status(self) -> GetCdRomRsp:
        """Check if the mounted image is in CD-ROM mode."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/storage/cdrom",
            response_model=GetCdRomRsp,
        )

    # ── Network (shared) ───────────────────────────────────────────────

    async def get_wifi_status(self) -> GetWifiRsp:
        """Get WiFi status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/network/wifi",
            response_model=GetWifiRsp,
        )

    async def connect_wifi(self, ssid: str, password: str) -> None:
        """Connect to a WiFi network."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/wifi/connect",
            data=ConnectWifiReq(ssid=ssid, password=password),
        )

    async def disconnect_wifi(self) -> None:
        """Disconnect from the current WiFi network."""
        await self._api_request_json(hdrs.METH_POST, "/network/wifi/disconnect")

    async def send_wake_on_lan(self, mac: str) -> None:
        """Send a Wake-on-LAN packet."""
        await self._api_request_json(
            hdrs.METH_POST, "/network/wol", data=WakeOnLANReq(mac=mac)
        )

    async def get_tailscale_status(self) -> GetTailscaleStatusRsp:
        """Get Tailscale status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/extensions/tailscale/status",
            response_model=GetTailscaleStatusRsp,
        )

    # ── Network (Pro only) ─────────────────────────────────────────────

    @require_hardware(HWVersion.PRO)
    async def get_static_ip(self) -> GetStaticIPRsp:
        """Get static IP configuration."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/network/static-ip",
            response_model=GetStaticIPRsp,
        )

    @require_hardware(HWVersion.PRO)
    async def set_static_ip(self, enabled: bool, ip: str) -> None:
        """Set static IP configuration."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/static-ip",
            data=SetStaticIPReq(enabled=enabled, ip=ip),
        )

    @require_hardware(HWVersion.PRO)
    async def scan_wifi(self) -> ScanWifiRsp:
        """Scan for available WiFi networks."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/network/wifi/scan",
            response_model=ScanWifiRsp,
        )

    # ── Stream (Pro only) ──────────────────────────────────────────────

    @require_hardware(HWVersion.PRO)
    async def set_rate_control_mode(self, mode: RateControlMode) -> None:
        """Set the stream rate control mode (CBR/VBR)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/rate-control",
            data=SetRateControlModeReq(mode=mode),
        )

    @require_hardware(HWVersion.PRO)
    async def set_stream_mode(self, mode: str) -> None:
        """Set the stream mode."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/mode",
            data=SetStreamModeReq(mode=mode),
        )

    @require_hardware(HWVersion.PRO)
    async def set_stream_quality(self, quality: int) -> None:
        """Set the stream quality / bit-rate."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/quality",
            data=SetStreamQualityReq(quality=quality),
        )

    @require_hardware(HWVersion.PRO)
    async def set_gop(self, gop: int) -> None:
        """Set the stream GOP (Group of Pictures)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/gop",
            data=SetGopReq(gop=gop),
        )

    @require_hardware(HWVersion.PRO)
    async def set_fps(self, fps: int) -> None:
        """Set the stream FPS."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/fps",
            data=SetFpsReq(fps=fps),
        )

    # ── Stream (shared) ────────────────────────────────────────────────

    def _parse_jpeg_from_bytes(self, data: bytes) -> Image:
        """Parse JPEG image from bytes."""
        return Image.open(io.BytesIO(data), formats=["JPEG"])

    async def mjpeg_stream(self) -> AsyncIterator[Image]:
        """Stream MJPEG frames."""
        async with self._request(hdrs.METH_GET, "/stream/mjpeg") as response:
            reader = MultipartReader.from_response(response)
            loop = asyncio.get_running_loop()

            async for part in reader:
                assert isinstance(part, BodyPartReader)
                data = await part.read()
                if not data:
                    _LOGGER.debug("Received empty MJPEG part, ending stream.")
                    break

                # Process image in executor to avoid blocking async loop
                image = await loop.run_in_executor(
                    None, self._parse_jpeg_from_bytes, data
                )
                yield image

    # ── Application ─────────────────────────────────────────────────────

    async def get_application_version(self) -> GetVersionRsp:
        """Get current and latest application versions."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/application/version",
            response_model=GetVersionRsp,
        )

    async def get_preview_status(self) -> GetPreviewRsp:
        """Check if preview updates are enabled."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/application/preview",
            response_model=GetPreviewRsp,
        )

    async def set_preview_state(self, enable: bool) -> None:
        """Enable or disable preview updates."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/application/preview",
            data=SetPreviewReq(enable=enable),
        )

    async def update_application(self) -> None:
        """Trigger the application update process."""
        await self._api_request_json(hdrs.METH_POST, "/application/update")

    # ── Download ────────────────────────────────────────────────────────

    async def is_image_download_enabled(self) -> ImageEnabledRsp:
        """Check if the /data partition allows downloads."""
        prefix = (
            "/storage/download" if self._hw_version == HWVersion.PRO else "/download"
        )
        return await self._api_request_json(
            hdrs.METH_GET,
            f"{prefix}/image/enabled",
            response_model=ImageEnabledRsp,
        )

    async def get_image_download_status(self) -> StatusImageRsp:
        """Get the status of an ongoing image download."""
        prefix = (
            "/storage/download" if self._hw_version == HWVersion.PRO else "/download"
        )
        return await self._api_request_json(
            hdrs.METH_GET,
            f"{prefix}/image/status",
            response_model=StatusImageRsp,
        )

    async def download_image(self, url: str) -> StatusImageRsp:
        """Start downloading an image from a URL."""
        prefix = (
            "/storage/download" if self._hw_version == HWVersion.PRO else "/download"
        )
        return await self._api_request_json(
            hdrs.METH_POST,
            f"{prefix}/image",
            response_model=StatusImageRsp,
            data=DownloadImageReq(file=url),
        )

    # ── Extensions (shared) ────────────────────────────────────────────

    async def tailscale_install(self) -> None:
        """Install Tailscale."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/install")

    async def tailscale_uninstall(self) -> None:
        """Uninstall Tailscale."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/uninstall")

    async def tailscale_up(self) -> None:
        """Bring Tailscale up."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/up")

    async def tailscale_down(self) -> None:
        """Bring Tailscale down."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/down")

    async def tailscale_login(self) -> None:
        """Log in to Tailscale."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/login")

    async def tailscale_logout(self) -> None:
        """Log out of Tailscale."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/logout")

    async def tailscale_start(self) -> None:
        """Start Tailscale service."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/start")

    async def tailscale_stop(self) -> None:
        """Stop Tailscale service."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/stop")

    async def tailscale_restart(self) -> None:
        """Restart Tailscale service."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/restart")

    # ── Extensions (Pro only) ──────────────────────────────────────────

    @require_hardware(HWVersion.PRO)
    async def assistant_install(self) -> None:
        """Install assistant dependencies."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/assistant/install")

    @require_hardware(HWVersion.PRO)
    async def assistant_start(self) -> None:
        """Start assistant."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/assistant/start")

    @require_hardware(HWVersion.PRO)
    async def kvmadmin_install(self) -> None:
        """Install kvmadmin."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/kvmadmin/install")

    @require_hardware(HWVersion.PRO)
    async def kvmadmin_uninstall(self) -> None:
        """Uninstall kvmadmin."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/kvmadmin/uninstall")

    @require_hardware(HWVersion.PRO)
    async def kvmadmin_start(self) -> None:
        """Start kvmadmin."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/kvmadmin/start")

    @require_hardware(HWVersion.PRO)
    async def kvmadmin_stop(self) -> None:
        """Stop kvmadmin."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/kvmadmin/stop")

    @require_hardware(HWVersion.PRO)
    async def kvmadmin_status(self) -> GetKvmadminStatusRsp:
        """Get kvmadmin status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/extensions/kvmadmin/status",
            response_model=GetKvmadminStatusRsp,
        )

    # ── Mouse (WebSocket) ──────────────────────────────────────────────

    async def _get_ws(self) -> aiohttp.ClientWebSocketResponse:
        """Get or create WebSocket connection for mouse events."""
        if self._ws is None or self._ws.closed:
            if not self._token:
                raise NanoKVMNotAuthenticatedError("Client is not authenticated")

            # WebSocket URL uses ws:// or wss:// scheme
            scheme = "ws" if self.url.scheme == "http" else "wss"
            ws_url = self.url.with_scheme(scheme) / "ws"

            assert self._session is not None
            assert self._ssl_config is not None

            self._ws = await self._session.ws_connect(
                str(ws_url),
                headers={"Cookie": f"nano-kvm-token={self._token}"},
                ssl=self._ssl_config,
            )
        return self._ws

    async def _send_mouse_event(
        self, event_type: int, button_state: int, x: float, y: float
    ) -> None:
        """
        Send a mouse event via WebSocket.

        Args:
            event_type: 0=mouse_up, 1=mouse_down, 2=move_abs, 3=move_rel, 4=scroll
            button_state: Button state (0=no buttons, 1=left, 2=right, 4=middle)
            x: X coordinate (0.0-1.0 for abs/rel/scroll) or 0.0 for button events
            y: Y coordinate (0.0-1.0 for abs/rel/scroll) or 0.0 for button events
        """
        ws = await self._get_ws()

        # Scale coordinates for absolute/relative movements and scroll
        if event_type in (2, 3, 4):  # move_abs, move_rel, or scroll
            x_val = int(x * 32768)
            y_val = int(y * 32768)
        else:
            x_val = int(x)
            y_val = int(y)

        # Message format: [2, event_type, button_state, x_val, y_val]
        # where 2 indicates mouse event
        message = [2, event_type, button_state, x_val, y_val]

        _LOGGER.debug("Sending mouse event: %s", message)
        await ws.send_json(message)

    async def mouse_move_abs(self, x: float, y: float) -> None:
        """
        Move mouse to absolute position.

        Args:
            x: X coordinate (0.0 to 1.0, left to right)
            y: Y coordinate (0.0 to 1.0, top to bottom)
        """
        await self._send_mouse_event(2, 0, x, y)

    async def mouse_move_rel(self, dx: float, dy: float) -> None:
        """
        Move mouse relative to current position.

        Args:
            dx: Horizontal movement (-1.0 to 1.0)
            dy: Vertical movement (-1.0 to 1.0)
        """
        await self._send_mouse_event(3, 0, dx, dy)

    async def mouse_down(self, button: MouseButton = MouseButton.LEFT) -> None:
        """
        Press a mouse button.

        Args:
            button: Mouse button to press (MouseButton.LEFT, MouseButton.RIGHT,
                MouseButton.MIDDLE)
        """
        await self._send_mouse_event(1, int(button), 0.0, 0.0)

    async def mouse_up(self) -> None:
        """
        Release a mouse button.

        Note: Mouse up event always uses button_state=0 per the NanoKVM protocol.
        """
        await self._send_mouse_event(0, 0, 0.0, 0.0)

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
                MouseButton.MIDDLE)
            x: Optional X coordinate (0.0 to 1.0) for absolute positioning
                before click
            y: Optional Y coordinate (0.0 to 1.0) for absolute positioning
                before click
        """
        # Move to position if coordinates provided
        if x is not None and y is not None:
            await self.mouse_move_abs(x, y)
            # Small delay to ensure position update
            await asyncio.sleep(0.05)

        # Send mouse down
        await self.mouse_down(button)
        # Small delay between down and up
        await asyncio.sleep(0.05)
        # Send mouse up
        await self.mouse_up()

    async def mouse_scroll(self, dx: float, dy: float) -> None:
        """
        Scroll the mouse wheel.

        Args:
            dx: Horizontal scroll amount (-1.0 to 1.0)
            dy: Vertical scroll amount (-1.0 to 1.0) # positive=up, negative=down)
        """
        await self._send_mouse_event(4, 0, dx, dy)
