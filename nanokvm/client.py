"""API client for NanoKVM."""

from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
)
import contextlib
import functools
import inspect
import logging
from os import PathLike
from pathlib import Path
import re
import ssl
import stat
from typing import Any, Literal, TypeVar, overload

import aiohttp
from aiohttp import ClientResponse, ClientSession, Fingerprint, hdrs
from aiohttp.payload import Payload
from PIL import Image
from pydantic import BaseModel
import yarl

from .components.mouse import MouseController
from .components.session import SessionController
from .components.stream import StreamController
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
from .utils import obfuscate_password as _obfuscate_password

T = TypeVar("T", bound=BaseModel)

_LOGGER = logging.getLogger(__name__)

PASTE_CHAR_MAP = set(
    "\t\n !\"#$%&'()*+,-./0123456789"
    ":;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
)

ImageTransferProgressCallback = Callable[
    [ImageTransferProgress], None | Awaitable[None]
]

_SESSION_COOKIE_NAME = "nano-kvm-token"


def obfuscate_password(password: str) -> str:
    """Keep the password helper available from the client module."""
    return _obfuscate_password(password)


async def _report_image_transfer_progress(
    callback: ImageTransferProgressCallback | None,
    bytes_transferred: int,
    total_bytes: int,
) -> None:
    """Invoke a synchronous or asynchronous image progress callback."""
    if callback is None:
        return

    percentage = 100 * bytes_transferred / total_bytes if total_bytes else 0
    result = callback(
        ImageTransferProgress(
            bytes_transferred=bytes_transferred,
            total_bytes=total_bytes,
            percentage=percentage,
        )
    )
    if inspect.isawaitable(result):
        await result


class _ImageProgressPayload(Payload):
    """Stream one multipart image field in bounded chunks with progress updates."""

    def __init__(
        self,
        file_path: Path,
        *,
        chunk_size: int,
        total_bytes: int,
        report_progress: Callable[[int], Awaitable[None]],
    ) -> None:
        super().__init__(file_path, content_type="application/octet-stream")
        self._file_path = file_path
        self._chunk_size = chunk_size
        self._total_bytes = total_bytes
        self._size = total_bytes
        self._report_progress = report_progress

    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        """A file payload has no eager string representation."""
        return "<streamed image>"

    async def write(self, writer: Any) -> None:
        """Write no more than one configured chunk at a time."""
        self._consumed = True
        bytes_transferred = 0
        with self._file_path.open("rb") as image_file:
            while bytes_transferred < self._total_bytes:
                chunk = image_file.read(
                    min(self._chunk_size, self._total_bytes - bytes_transferred)
                )
                if not chunk:
                    raise OSError("image changed while it was being uploaded")

                await writer.write(chunk)
                bytes_transferred += len(chunk)
                if bytes_transferred < self._total_bytes:
                    await self._report_progress(bytes_transferred)

            if image_file.read(1):
                raise OSError("image changed while it was being uploaded")


class NanoKVMError(Exception):
    """Base exception for NanoKVM client errors."""


class NanoKVMNotAuthenticatedError(NanoKVMError):
    """Exception for authentication errors."""


class NanoKVMPermissionError(NanoKVMError):
    """Exception for an authenticated request forbidden by the device."""

    def __init__(
        self,
        message: str = "NanoKVM permission denied",
        *,
        status: int = 403,
        method: str,
        path: str,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.method = method.upper()
        self.path = path


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

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
_BINARY_MOUSE_MIN_NON_PRO_VERSION = "2.3.2"
_BINARY_MOUSE_MIN_PRO_VERSION = "1.2.6"
_CURRENT_PASSWORD_MIN_NON_PRO_VERSION = "2.5.1"


def _parse_version(version: str) -> tuple[int, ...] | None:
    """Parse simple semantic app versions; return None for custom/dev builds."""
    match = _VERSION_RE.fullmatch(version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _version_at_least(version: str, minimum: str) -> bool:
    """Return True when version is unknown or at least the requested version."""
    parsed_version = _parse_version(version)
    parsed_minimum = _parse_version(minimum)
    if parsed_version is None or parsed_minimum is None:
        return True

    length = max(len(parsed_version), len(parsed_minimum))
    normalized_version = parsed_version + (0,) * (length - len(parsed_version))
    normalized_minimum = parsed_minimum + (0,) * (length - len(parsed_minimum))
    return normalized_version >= normalized_minimum


def _validate_sha256(sha256: str | None) -> None:
    """Validate an optional SHA-256 checksum before device I/O."""
    if sha256 is not None and re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None:
        raise ValueError("sha256 must contain 64 hexadecimal characters")


def _validate_sha256_argument(func: F) -> F:
    """Validate a keyword-only ``sha256`` argument before version checks."""

    @functools.wraps(func)
    async def wrapper(self: NanoKVMClient, *args: Any, **kwargs: Any) -> Any:
        _validate_sha256(kwargs.get("sha256"))
        return await func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def require_hardware(*requirements: HWVersion | HWFamily) -> Callable[[F], F]:
    """Restrict a method to specific hardware versions or families."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(self: NanoKVMClient, *args: Any, **kwargs: Any) -> Any:
            if self._hw_version is None:
                raise NanoKVMError(
                    f"{func.__name__} requires hardware detection; "
                    f"call detect_hardware() first"
                )
            family = self._hw_version.family
            matches = any(
                (isinstance(requirement, HWFamily) and family is requirement)
                or (
                    isinstance(requirement, HWVersion)
                    and self._hw_version is requirement
                )
                for requirement in requirements
            )
            if not matches:
                allowed = ", ".join(requirement.value for requirement in requirements)
                if all(
                    isinstance(requirement, HWFamily) for requirement in requirements
                ):
                    detected = (
                        family.value if family is not None else self._hw_version.value
                    )
                    message = (
                        f"{func.__name__} requires hardware family: {allowed} "
                        f"(detected: {detected})"
                    )
                else:
                    message = (
                        f"{func.__name__} requires hardware: {allowed} "
                        f"(detected: {self._hw_version})"
                    )
                raise NanoKVMNotSupportedError(message)
            return await func(self, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def require_application_version(
    *,
    non_pro: str | None = None,
    pro: str | None = None,
) -> Callable[[F], F]:
    """Decorator that restricts a method to minimum application versions."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(self: NanoKVMClient, *args: Any, **kwargs: Any) -> Any:
            if self._hw_version is None:
                if self._token is None:
                    return await func(self, *args, **kwargs)
                await self.detect_hardware()

            assert self._hw_version is not None
            minimum = pro if self._is_hardware_family(HWFamily.PRO) else non_pro
            if minimum is None:
                return await func(self, *args, **kwargs)

            if self._application_version is None:
                if self._token is None:
                    return await func(self, *args, **kwargs)
                await self.detect_versions()

            if self._application_version is not None and not _version_at_least(
                self._application_version, minimum
            ):
                hardware_family = (
                    "Pro" if self._is_hardware_family(HWFamily.PRO) else "non-Pro"
                )
                raise NanoKVMNotSupportedError(
                    f"{func.__name__} requires {hardware_family} application "
                    f"version >= {minimum} (detected: {self._application_version})"
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
        self._session_generation = 0
        self._request_timeout = request_timeout
        self._image_transfer_lock = asyncio.Lock()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
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
        timeout: aiohttp.ClientTimeout | None = None,
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
        data: aiohttp.FormData,
        **kwargs: Any,
    ) -> T: ...

    @overload
    async def _api_request_form(
        self,
        method: str,
        path: str,
        response_model: None = None,
        data: aiohttp.FormData | None = None,
        **kwargs: Any,
    ) -> None: ...

    async def _api_request_form(
        self,
        method: str,
        path: str,
        response_model: type[T] | None = None,
        data: aiohttp.FormData | None = None,
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

    @require_application_version(non_pro="2.2.6")
    async def get_hostname(self) -> GetHostnameRsp:
        """Get the configured hostname."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hostname",
            response_model=GetHostnameRsp,
        )

    @require_application_version(non_pro="2.2.6")
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

    async def get_scripts(self) -> GetScriptsRsp:
        """Get the list of uploaded scripts."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/script",
            response_model=GetScriptsRsp,
        )

    async def upload_script(self, file_path: str | PathLike[str]) -> UploadScriptRsp:
        """Upload a script file."""
        return await self._upload_file(
            "/vm/script/upload",
            file_path,
            response_model=UploadScriptRsp,
        )

    async def run_script(self, name: str, script_type: RunScriptType) -> RunScriptRsp:
        """Run an uploaded script."""
        return await self._api_request_json(
            hdrs.METH_POST,
            "/vm/script/run",
            response_model=RunScriptRsp,
            data=RunScriptReq(name=name, type=script_type),
        )

    async def delete_script(self, name: str) -> None:
        """Delete an uploaded script."""
        await self._api_request_json(
            hdrs.METH_DELETE,
            "/vm/script",
            data=DeleteScriptReq(name=name),
        )

    async def push_button(self, button: GpioType, duration_ms: int) -> None:
        """Simulate pushing a hardware button."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/gpio",
            data=SetGpioReq(type=button, duration=duration_ms),
        )

    @require_application_version(non_pro="2.1.6")
    async def get_ssh_state(self) -> GetSSHStateRsp:
        """Get SSH enabled state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/ssh",
            response_model=GetSSHStateRsp,
        )

    @require_application_version(non_pro="2.1.6")
    async def enable_ssh(self) -> None:
        """Enable SSH server."""
        await self._api_request_json(hdrs.METH_POST, "/vm/ssh/enable")

    @require_application_version(non_pro="2.1.6")
    async def disable_ssh(self) -> None:
        """Disable SSH server."""
        await self._api_request_json(hdrs.METH_POST, "/vm/ssh/disable")

    @require_application_version(non_pro="2.2.2")
    async def get_mdns_state(self) -> GetMdnsStateRsp:
        """Get mDNS enabled state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/mdns",
            response_model=GetMdnsStateRsp,
        )

    @require_application_version(non_pro="2.2.2")
    async def enable_mdns(self) -> None:
        """Enable mDNS."""
        await self._api_request_json(hdrs.METH_POST, "/vm/mdns/enable")

    @require_application_version(non_pro="2.2.2")
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

    @require_application_version(non_pro="2.2.6")
    async def get_mouse_jiggler_state(self) -> GetMouseJigglerRsp:
        """Get the mouse jiggler state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/mouse-jiggler",
            response_model=GetMouseJigglerRsp,
        )

    @require_application_version(non_pro="2.2.6")
    async def set_mouse_jiggler_state(
        self, enabled: bool, mode: MouseJigglerMode
    ) -> None:
        """Set the mouse jiggler state."""
        current = await self.get_mouse_jiggler_state()
        if current.enabled == enabled and (not enabled or current.mode == mode):
            return

        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/mouse-jiggler/",
            data=SetMouseJigglerReq(enabled=enabled, mode=mode),
        )

    @require_application_version(non_pro="2.2.6")
    async def get_web_title(self) -> GetWebTitleRsp:
        """Get the web page title."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/web-title",
            response_model=GetWebTitleRsp,
        )

    @require_application_version(non_pro="2.2.6")
    async def set_web_title(self, title: str) -> None:
        """Set the web page title."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/web-title",
            data=SetWebTitleReq(title=title),
        )

    @require_application_version(non_pro="2.2.2")
    async def reboot_system(self) -> None:
        """Reboot the KVM device."""
        await self._api_request_json(hdrs.METH_POST, "/vm/system/reboot")

    @require_hardware(HWFamily.PRO)
    async def switch_to_pikvm(self) -> None:
        """Switch the system image to PiKVM."""
        await self._api_request_json(hdrs.METH_POST, "/vm/system/pikvm")

    # ── VM (non-Pro only) ──────────────────────────────────────────────

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def get_input_region(self) -> GetInputRegionRsp:
        """Get the configured non-Pro input region."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/input-region",
            response_model=GetInputRegionRsp,
        )

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
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/input-region",
            data=SetInputRegionReq(
                mode=mode,
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
            ),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def get_input_resolution(self) -> GetInputResolutionRsp:
        """Get the current non-Pro input frame resolution."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/input-resolution",
            response_model=GetInputResolutionRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    async def set_screen(self, setting: ScreenSettingType, value: int) -> None:
        """Set a non-Pro NanoKVM screen setting."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/screen",
            data=SetScreenReq(type=setting, value=value),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.6")
    async def get_swap_size(self) -> int:
        """Get Swap size."""
        rsp = await self._api_request_json(
            hdrs.METH_GET,
            "/vm/swap",
            response_model=GetSwapSizeRsp,
        )
        return rsp.size

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.6")
    async def set_swap_size(self, size_mb: int) -> None:
        """Set the Swap size."""
        await self._api_request_json(
            hdrs.METH_POST, "/vm/swap", data=SetSwapSizeReq(size=size_mb)
        )

    @require_hardware(HWFamily.NON_PRO)
    async def get_memory_limit(self) -> GetMemoryLimitRsp:
        """Get the configured Go memory limit."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/memory/limit",
            response_model=GetMemoryLimitRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    async def set_memory_limit(self, enabled: bool, limit_mb: int) -> None:
        """Set or disable the Go memory limit."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/memory/limit",
            data=SetMemoryLimitReq(enabled=enabled, limit=limit_mb),
        )

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def get_hdmi_state(self) -> GetHdmiStateRsp:
        """Get the HDMI state (PCIe variant)."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi",
            response_model=GetHdmiStateRsp,
        )

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.1.5")
    async def reset_hdmi(self) -> None:
        """Reset the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/reset")

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def enable_hdmi(self) -> None:
        """Enable the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/enable")

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def disable_hdmi(self) -> None:
        """Disable the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/disable")

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.5.0")
    async def set_hdmi_idle_timeout(self, minutes: int) -> None:
        """Set the HDMI capture idle timeout in minutes; zero disables it."""
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise ValueError("minutes must be an integer between 0 and 10080")
        if not 0 <= minutes <= 10080:
            raise ValueError("minutes must be between 0 and 10080")

        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/timeout",
            data=SetHdmiIdleTimeoutReq(minutes=minutes),
        )

    # ── VM (Pro only) ──────────────────────────────────────────────────

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def refresh_virtual_device(self, device: str) -> None:
        """Refresh a virtual device (e.g. emmc)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/device/virtual/refresh",
            data=RefreshVirtualDeviceReq(device=device),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_lcd_time_format(self) -> GetLcdTimeFormatRsp:
        """Get the LCD time format."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/lcd/time/format",
            response_model=GetLcdTimeFormatRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def set_lcd_time_format(self, fmt: LcdTimeFormat | str) -> None:
        """Set the LCD time format (12h/24h)."""
        format_value = fmt if isinstance(fmt, LcdTimeFormat) else LcdTimeFormat(fmt)
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/lcd/time/format",
            data=SetLcdTimeFormatReq(format=format_value),
        )

    @require_hardware(HWFamily.PRO)
    async def get_hdmi_capture(self) -> GetHdmiCaptureRsp:
        """Get HDMI capture status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi/capture",
            response_model=GetHdmiCaptureRsp,
        )

    @require_hardware(HWFamily.PRO)
    async def set_hdmi_capture(self, enabled: bool) -> None:
        """Set HDMI capture status."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/capture",
            data=SetHdmiCaptureReq(enabled=enabled),
        )

    @require_hardware(HWFamily.PRO)
    async def get_hdmi_passthrough(self) -> GetHdmiPassthroughRsp:
        """Get HDMI passthrough status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi/passthrough",
            response_model=GetHdmiPassthroughRsp,
        )

    @require_hardware(HWFamily.PRO)
    async def set_hdmi_passthrough(self, enabled: bool) -> None:
        """Set HDMI passthrough status."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/passthrough",
            data=SetHdmiPassthroughReq(enabled=enabled),
        )

    @require_hardware(HWFamily.PRO)
    async def get_edid(self) -> GetEdidRsp:
        """Get current EDID."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/edid",
            response_model=GetEdidRsp,
        )

    @require_hardware(HWFamily.PRO)
    async def switch_edid(self, edid: EdidValue) -> None:
        """Switch EDID."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/edid",
            data=SwitchEdidReq(edid=edid),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def get_custom_edid_list(self) -> GetCustomEdidListRsp:
        """Get custom EDID list."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/edid/custom",
            response_model=GetCustomEdidListRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def upload_edid(self, file_path: str | PathLike[str]) -> UploadEdidRsp:
        """Upload a custom EDID."""
        return await self._upload_file(
            "/vm/edid/upload",
            file_path,
            response_model=UploadEdidRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def delete_edid(self, edid: str) -> None:
        """Delete a custom EDID."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/edid/delete",
            data=DeleteEdidReq(edid=edid),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def get_low_power(self) -> GetLowPowerRsp:
        """Get low power status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/low-power",
            response_model=GetLowPowerRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def set_low_power(self, enable: bool) -> None:
        """Set low power mode."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/low-power",
            data=SetLowPowerReq(enable=enable),
        )

    @require_hardware(HWFamily.PRO)
    async def get_led_strip(self) -> GetLedStripRsp:
        """Get LED strip configuration."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/ledstrip/get",
            response_model=GetLedStripRsp,
        )

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
        if all(
            value is None
            for value in (on, horizontal_count, vertical_count, brightness)
        ):
            raise ValueError("At least one LED strip setting must be provided")

        if any(
            value is None
            for value in (on, horizontal_count, vertical_count, brightness)
        ):
            current = await self.get_led_strip()
            on = current.on if on is None else on
            horizontal_count = (
                current.horizontal_count
                if horizontal_count is None
                else horizontal_count
            )
            vertical_count = (
                current.vertical_count if vertical_count is None else vertical_count
            )
            brightness = current.brightness if brightness is None else brightness

        assert on is not None
        assert horizontal_count is not None
        assert vertical_count is not None
        assert brightness is not None

        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/ledstrip/set",
            data=SetLedStripReq.model_validate(
                {
                    "on": on,
                    "hor": horizontal_count,
                    "ver": vertical_count,
                    "brightness": brightness,
                }
            ),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_timezone(self) -> GetTimeZoneRsp:
        """Get the configured timezone."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/timezone",
            response_model=GetTimeZoneRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def set_timezone(self, timezone: str) -> None:
        """Set the timezone."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/timezone",
            data=SetTimeZoneReq(timezone=timezone),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_time_status(self) -> GetTimeStatusRsp:
        """Get time synchronization status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/time/status",
            response_model=GetTimeStatusRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def sync_time(self) -> None:
        """Synchronize time."""
        await self._api_request_json(hdrs.METH_POST, "/vm/time/sync")

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.10")
    async def get_menubar_config(self) -> GetMenuBarConfigRsp:
        """Get menu bar configuration."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/menubar",
            response_model=GetMenuBarConfigRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.10")
    async def set_menubar_config(self, disabled_items: list[str]) -> None:
        """Set menu bar configuration."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/menubar",
            data=SetMenuBarConfigReq.model_validate({"disabledItems": disabled_items}),
        )

    # ── HID ─────────────────────────────────────────────────────────────

    @require_application_version(non_pro="2.2.5")
    async def get_hid_mode(self) -> GetHidModeRsp:
        """Get the current HID mode."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/hid/mode",
            response_model=GetHidModeRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def get_keyboard_led_status(self) -> GetKeyboardLedStatusRsp:
        """Get host keyboard LED state on non-Pro firmware 2.5.0 and newer.

        When ``known`` is false, the host has not reported its LED state yet.
        """
        return await self._api_request_json(
            hdrs.METH_GET,
            "/hid/leds",
            response_model=GetKeyboardLedStatusRsp,
        )

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def get_shortcuts(self) -> GetShortcutsRsp:
        """Get configured custom HID shortcuts."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/hid/shortcuts",
            response_model=GetShortcutsRsp,
        )

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def add_shortcut(self, keys: list[ShortcutKey]) -> None:
        """Add a custom HID shortcut."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/hid/shortcut",
            data=AddShortcutReq(keys=keys),
        )

    @require_application_version(non_pro="2.3.2", pro="1.2.8")
    async def delete_shortcut(self, shortcut_id: str) -> None:
        """Delete a custom HID shortcut."""
        await self._api_request_json(
            hdrs.METH_DELETE,
            "/hid/shortcut",
            data=DeleteShortcutReq(id=shortcut_id),
        )

    @require_application_version(non_pro="2.3.4", pro="1.2.12")
    async def get_leader_key(self) -> GetLeaderKeyRsp:
        """Get the configured shortcut leader key."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/hid/shortcut/leader-key",
            response_model=GetLeaderKeyRsp,
        )

    @require_application_version(non_pro="2.3.4", pro="1.2.12")
    async def set_leader_key(self, key: str = "") -> None:
        """Set or clear the shortcut leader key."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/hid/shortcut/leader-key",
            data=SetLeaderKeyReq(key=key),
        )

    @require_application_version(non_pro="2.2.5")
    async def set_hid_mode(self, mode: HidMode) -> None:
        """Set the HID mode (requires reboot)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/hid/mode",
            data=SetHidModeReq(mode=mode),
        )

    @require_application_version(pro="1.1.6")
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

    async def upload_image(
        self,
        file_path: str | PathLike[str],
        *,
        progress_callback: ImageTransferProgressCallback | None = None,
        sha256: str | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        """Upload one local image, serializing transfers on this client."""
        async with self._image_transfer_lock:
            await self._upload_image(
                file_path,
                progress_callback=progress_callback,
                sha256=sha256,
                chunk_size=chunk_size,
            )

    async def _upload_image(
        self,
        file_path: str | PathLike[str],
        *,
        progress_callback: ImageTransferProgressCallback | None = None,
        sha256: str | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        """Upload a local image, streaming on non-Pro and chunking on Pro."""
        image_path = Path(file_path)
        file_stat = image_path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("file_path must point to a regular file")
        if (
            isinstance(chunk_size, bool)
            or not isinstance(chunk_size, int)
            or chunk_size <= 0
        ):
            raise ValueError("chunk_size must be a positive integer")
        _validate_sha256(sha256)

        if self._hw_version is None or self._hw_version.family is None:
            raise NanoKVMNotSupportedError(
                "upload_image requires detected supported hardware"
            )

        total_bytes = file_stat.st_size
        is_pro = self._is_hardware_family(HWFamily.PRO)
        if is_pro and sha256 is not None:
            raise NanoKVMNotSupportedError(
                "upload_image does not support sha256 on NanoKVM Pro"
            )

        transfer_timeout = aiohttp.ClientTimeout(
            total=None,
            sock_connect=self._request_timeout,
            sock_read=self._request_timeout,
        )

        if not is_pro:
            if ".." in image_path.name:
                raise ValueError("non-Pro image filename must not contain '..'")
            if re.fullmatch(r"[A-Za-z0-9._-]+", image_path.name) is None:
                raise ValueError(
                    "non-Pro image filename must use ASCII letters, numbers, "
                    "dot, underscore, or hyphen"
                )
            if not image_path.name.lower().endswith(".iso"):
                raise ValueError("non-Pro image filename must end with .iso")

            await self._ensure_image_transfer_version("2.3.1")
            if sha256 is not None:
                await self._ensure_image_transfer_version("2.5.0")
        else:
            if not all(
                character in " -_." or character.isalnum()
                for character in image_path.name
            ):
                raise ValueError(
                    "Pro image filename must already be sanitized by firmware rules"
                )
            if not image_path.name.lower().endswith((".iso", ".img")):
                raise ValueError("Pro image filename must end with .iso or .img")

            remote_images = await self.get_images()
            remote_file = f"/data/{image_path.name}"
            if remote_file in remote_images.files:
                raise FileExistsError(
                    f"NanoKVM Pro already contains an image named {image_path.name}"
                )

        await _report_image_transfer_progress(progress_callback, 0, total_bytes)

        if is_pro:
            total_chunks = max(1, (total_bytes + chunk_size - 1) // chunk_size)
            bytes_transferred = 0
            upload_started = False
            try:
                with image_path.open("rb", buffering=0) as image_file:
                    for chunk_index in range(total_chunks):
                        expected_size = min(
                            chunk_size, total_bytes - chunk_index * chunk_size
                        )
                        chunk_parts: list[bytes] = []
                        bytes_read = 0
                        while bytes_read < expected_size:
                            part = image_file.read(expected_size - bytes_read)
                            if not part:
                                break
                            chunk_parts.append(part)
                            bytes_read += len(part)
                        chunk = b"".join(chunk_parts)
                        if len(chunk) != expected_size:
                            raise OSError("image changed while it was being uploaded")
                        if chunk_index == total_chunks - 1 and image_file.read(1):
                            raise OSError("image changed while it was being uploaded")

                        form = aiohttp.FormData(quote_fields=False)
                        form.add_field("chunkIndex", str(chunk_index))
                        form.add_field("chunkSize", str(chunk_size))
                        form.add_field("totalChunks", str(total_chunks))
                        form.add_field(
                            "file",
                            chunk,
                            filename=image_path.name,
                            content_type="application/octet-stream",
                        )
                        upload_started = True
                        await self._api_request_form(
                            hdrs.METH_POST,
                            "/storage/image/upload",
                            data=form,
                            timeout=transfer_timeout,
                        )
                        bytes_transferred += len(chunk)
                        await _report_image_transfer_progress(
                            progress_callback, bytes_transferred, total_bytes
                        )
            except BaseException:
                if upload_started:
                    with contextlib.suppress(BaseException):
                        await asyncio.shield(self.delete_image(remote_file))
                raise
            return

        async def report_progress(bytes_transferred: int) -> None:
            await _report_image_transfer_progress(
                progress_callback, bytes_transferred, total_bytes
            )

        form = aiohttp.FormData()
        form.add_field(
            "file",
            _ImageProgressPayload(
                image_path,
                chunk_size=chunk_size,
                total_bytes=total_bytes,
                report_progress=report_progress,
            ),
            filename=image_path.name,
            content_type="application/octet-stream",
        )
        headers = {"X-SHA256-Sum": sha256} if sha256 is not None else {}
        await self._api_request_form(
            hdrs.METH_POST,
            "/download/file",
            data=form,
            headers=headers,
            timeout=transfer_timeout,
        )
        await _report_image_transfer_progress(
            progress_callback, total_bytes, total_bytes
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
            data=MountImageReq.model_validate(
                {
                    "file": file,
                    "cdrom": cdrom if file else None,
                    "readOnly": read_only if file else None,
                }
            ),
        )

    @require_application_version(non_pro="2.3.0", pro="1.1.6")
    async def delete_image(self, file: str) -> None:
        """Delete an image file."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/storage/image/delete",
            data=DeleteImageReq(file=file),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.1")
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

    @require_application_version(non_pro="2.3.1")
    async def connect_wifi(self, ssid: str, password: str) -> None:
        """Connect to a WiFi network."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/wifi/connect",
            data=ConnectWifiReq(ssid=ssid, password=password),
        )

    async def connect_wifi_no_auth(
        self,
        ssid: str,
        password: str = "",
        ap_password: str | None = None,
    ) -> None:
        """Connect to WiFi while the device is in AP-mode setup flow."""
        headers = {"X-AP-Key": ap_password} if ap_password is not None else {}
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/wifi",
            authenticate=False,
            headers=headers,
            data=ConnectWifiReq(ssid=ssid, password=password),
        )

    @require_application_version(non_pro="2.3.6", pro="1.2.14")
    async def verify_ap_login(self, ap_password: str) -> None:
        """Verify AP-mode setup credentials."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/wifi/verify",
            authenticate=False,
            headers={"X-AP-Key": ap_password},
        )

    @require_application_version(non_pro="2.3.1")
    async def disconnect_wifi(self) -> None:
        """Disconnect from the current WiFi network."""
        await self._api_request_json(hdrs.METH_POST, "/network/wifi/disconnect")

    async def send_wake_on_lan(self, mac: str) -> None:
        """Send a Wake-on-LAN packet."""
        await self._api_request_json(
            hdrs.METH_POST, "/network/wol", data=WakeOnLANReq(mac=mac)
        )

    async def get_wol_macs(self) -> GetMacRsp:
        """Get saved Wake-on-LAN MAC entries."""
        try:
            return await self._api_request_json(
                hdrs.METH_GET,
                "/network/wol/mac",
                response_model=GetMacRsp,
            )
        except NanoKVMApiError as err:
            if (
                err.code == ApiResponseCode.INVALID_USERNAME_OR_PASSWORD.value
                and err.msg == "open file error"
            ):
                return GetMacRsp()
            raise

    async def delete_wol_mac(self, mac: str) -> None:
        """Delete a saved Wake-on-LAN MAC entry."""
        await self._api_request_json(
            hdrs.METH_DELETE,
            "/network/wol/mac",
            data=DeleteMacReq(mac=mac),
        )

    @require_application_version(non_pro="2.2.6")
    async def set_wol_mac_name(self, mac: str, name: str) -> None:
        """Set the display name for a saved Wake-on-LAN MAC entry."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/wol/mac/name",
            data=SetMacNameReq(mac=mac, name=name),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.4.1")
    async def get_dns(self) -> GetDNSRsp:
        """Get DNS configuration."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/network/dns",
            response_model=GetDNSRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.4.1")
    async def set_dns(
        self, mode: DNSMode | str, servers: list[str] | None = None
    ) -> None:
        """Set DNS configuration."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/dns",
            data=SetDNSReq(mode=DNSMode(mode), servers=servers or []),
        )

    @require_application_version(non_pro="2.1.6")
    async def get_tailscale_status(self) -> GetTailscaleStatusRsp:
        """Get Tailscale status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/extensions/tailscale/status",
            response_model=GetTailscaleStatusRsp,
        )

    # ── Network (Pro only) ─────────────────────────────────────────────

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def get_static_ip(self) -> GetStaticIPRsp:
        """Get static IP configuration."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/network/static-ip",
            response_model=GetStaticIPRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def set_static_ip(self, enabled: bool, ip: str) -> None:
        """Set static IP configuration."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/static-ip",
            data=SetStaticIPReq(enabled=enabled, ip=ip),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.7")
    async def scan_wifi(self) -> ScanWifiRsp:
        """Scan for available WiFi networks."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/network/wifi/scan",
            response_model=ScanWifiRsp,
        )

    # ── Stream (Pro only) ──────────────────────────────────────────────

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.6")
    async def set_rate_control_mode(self, mode: RateControlMode) -> None:
        """Set the stream rate control mode (CBR/VBR)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/rate-control",
            data=SetRateControlModeReq(mode=mode),
        )

    @require_hardware(HWFamily.PRO)
    async def set_stream_mode(self, mode: StreamMode | str) -> None:
        """Set the stream mode."""
        stream_mode = mode if isinstance(mode, StreamMode) else StreamMode(mode)
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/mode",
            data=SetStreamModeReq(mode=stream_mode),
        )

    @require_hardware(HWFamily.PRO)
    async def set_stream_quality(self, quality: int) -> None:
        """Set the stream quality / bit-rate."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/quality",
            data=SetStreamQualityReq(quality=quality),
        )

    @require_hardware(HWFamily.PRO)
    async def set_gop(self, gop: int) -> None:
        """Set the stream GOP (Group of Pictures)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/gop",
            data=SetGopReq(gop=gop),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.8")
    async def set_fps(self, fps: int) -> None:
        """Set the stream FPS."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/stream/fps",
            data=SetFpsReq(fps=fps),
        )

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
        return await self._api_request_json(
            hdrs.METH_GET,
            "/application/version",
            response_model=GetVersionRsp,
        )

    @require_application_version(non_pro="2.2.5")
    async def get_preview_status(self) -> GetPreviewRsp:
        """Check if preview updates are enabled."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/application/preview",
            response_model=GetPreviewRsp,
        )

    @require_application_version(non_pro="2.2.5")
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

    def _image_download_prefix(self) -> str:
        """Return the image download route prefix for the detected hardware."""
        if self._is_hardware_family(HWFamily.PRO):
            return "/storage/download"
        return "/download"

    @require_application_version(non_pro="2.1.6")
    async def is_image_download_enabled(self) -> ImageEnabledRsp:
        """Check if the /data partition allows downloads."""
        prefix = self._image_download_prefix()
        return await self._api_request_json(
            hdrs.METH_GET,
            f"{prefix}/image/enabled",
            response_model=ImageEnabledRsp,
        )

    @require_application_version(non_pro="2.1.6")
    async def get_image_download_status(self) -> StatusImageRsp:
        """Get the status of an ongoing image download."""
        prefix = self._image_download_prefix()
        return await self._api_request_json(
            hdrs.METH_GET,
            f"{prefix}/image/status",
            response_model=StatusImageRsp,
        )

    @require_application_version(non_pro="2.5.0")
    @require_hardware(HWFamily.NON_PRO)
    async def cancel_image_download(self) -> None:
        """Cancel an active non-Pro image download."""
        await self._api_request_json(hdrs.METH_POST, "/download/image/cancel")

    async def watch_image_download(
        self, *, poll_interval: float = 1.0
    ) -> AsyncIterator[StatusImageRsp]:
        """Poll image-download status until it reaches a terminal state."""
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than zero")

        terminal_statuses = {
            DownloadStatus.IDLE,
            DownloadStatus.SUCCESS,
            DownloadStatus.FAILED,
            DownloadStatus.CHECKSUM_FAILED,
        }
        while True:
            status = await self.get_image_download_status()
            yield status
            if status.status in terminal_statuses:
                return
            await asyncio.sleep(poll_interval)

    @_validate_sha256_argument
    @require_application_version(non_pro="2.1.6")
    async def download_image(
        self, url: str, *, sha256: str | None = None
    ) -> StatusImageRsp:
        """Start downloading an image from a URL."""
        if sha256 is not None:
            if (
                self._hw_version is None
                or self._hw_version.family is not HWFamily.NON_PRO
            ):
                raise NanoKVMNotSupportedError(
                    "download_image sha256 is only supported on non-Pro hardware"
                )
            await self._ensure_image_transfer_version("2.5.0")

        prefix = self._image_download_prefix()
        return await self._api_request_json(
            hdrs.METH_POST,
            f"{prefix}/image",
            response_model=StatusImageRsp,
            data=DownloadImageReq(file=url, sha256sum=sha256),
        )

    # ── MCP and coordinated control (non-Pro only) ─────────────────────

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def get_mcp_config(self) -> GetMCPConfigRsp:
        """Get MCP configuration and coordinated-control state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/mcp/config",
            response_model=GetMCPConfigRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def set_mcp_enabled(self, enabled: bool) -> GetMCPConfigRsp:
        """Enable or disable MCP control."""
        return await self._api_request_json(
            hdrs.METH_POST,
            "/mcp/config",
            response_model=GetMCPConfigRsp,
            data=SetMCPConfigReq(enabled=enabled),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def regenerate_mcp_api_key(self) -> GetMCPConfigRsp:
        """Generate and return a new MCP API key."""
        return await self._api_request_json(
            hdrs.METH_POST,
            "/mcp/key/regenerate",
            response_model=GetMCPConfigRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def get_ai_control_status(self) -> AIControlStatusRsp:
        """Get the current coordinated-control owner and transition state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/ai/control/status",
            response_model=AIControlStatusRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.0")
    async def set_ai_control_mode(self, mode: AIControlMode) -> SetAIControlModeRsp:
        """Select the owner of coordinated input control."""
        return await self._api_request_json(
            hdrs.METH_PUT,
            "/ai/control/mode",
            response_model=SetAIControlModeRsp,
            data=SetAIControlModeReq(mode=mode),
        )

    # ── Extensions (shared) ────────────────────────────────────────────

    @require_application_version(non_pro="2.1.6")
    async def tailscale_install(self) -> None:
        """Install Tailscale."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/install")

    @require_application_version(non_pro="2.1.6")
    async def tailscale_uninstall(self) -> None:
        """Uninstall Tailscale."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/uninstall")

    @require_application_version(non_pro="2.1.6")
    async def tailscale_up(self) -> None:
        """Bring Tailscale up."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/up")

    @require_application_version(non_pro="2.1.6")
    async def tailscale_down(self) -> None:
        """Bring Tailscale down."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/down")

    @require_application_version(non_pro="2.1.6")
    async def tailscale_login(self) -> LoginTailscaleRsp:
        """Log in to Tailscale."""
        return await self._api_request_json(
            hdrs.METH_POST,
            "/extensions/tailscale/login",
            response_model=LoginTailscaleRsp,
        )

    @require_application_version(non_pro="2.1.6")
    async def tailscale_logout(self) -> None:
        """Log out of Tailscale."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/logout")

    @require_application_version(non_pro="2.1.6")
    async def tailscale_start(self) -> None:
        """Start Tailscale service."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/start")

    @require_application_version(non_pro="2.1.6")
    async def tailscale_stop(self) -> None:
        """Stop Tailscale service."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/stop")

    @require_application_version(non_pro="2.1.6")
    async def tailscale_restart(self) -> None:
        """Restart Tailscale service."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/restart")

    # ── Extensions (Pro only) ──────────────────────────────────────────

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.4")
    async def assistant_install(self) -> None:
        """Install assistant dependencies."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/assistant/install")

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.4")
    async def assistant_start(self) -> None:
        """Start assistant."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/assistant/start")

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_install(self) -> None:
        """Install kvmadmin."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/kvmadmin/install")

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_uninstall(self) -> None:
        """Uninstall kvmadmin."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/kvmadmin/uninstall")

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_start(self) -> None:
        """Start kvmadmin."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/kvmadmin/start")

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_stop(self) -> None:
        """Stop kvmadmin."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/kvmadmin/stop")

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.5")
    async def kvmadmin_status(self) -> GetKvmadminStatusRsp:
        """Get kvmadmin status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/extensions/kvmadmin/status",
            response_model=GetKvmadminStatusRsp,
        )

    # ── Mouse (WebSocket) ──────────────────────────────────────────────

    async def _close_ws(self) -> None:
        """Close and forget the current WebSocket connection."""
        await self._mouse.close_ws()

    async def _invalidate_ws(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Forget a failed WebSocket without closing a replacement connection."""
        await self._mouse.invalidate_ws(ws)

    async def _get_ws(self) -> aiohttp.ClientWebSocketResponse:
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
        send: Callable[[aiohttp.ClientWebSocketResponse], Awaitable[None]],
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
