"""API client for NanoKVM."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
import io
import json
import logging
from typing import Any, TypeVar, overload

import aiohttp
from aiohttp import BodyPartReader, ClientResponse, ClientSession, MultipartReader, hdrs
from PIL import Image
from pydantic import BaseModel, ValidationError
import yarl

from .models import (
    ApiResponse,
    ApiResponseCode,
    ChangePasswordReq,
    ConnectWifiReq,
    DownloadImageReq,
    GetAccountRsp,
    GetCdRomRsp,
    GetGpioRsp,
    GetHardwareRsp,
    GetHdmiStateRsp,
    GetHidModeRsp,
    GetInfoRsp,
    GetMdnsStateRsp,
    GetMemoryLimitRsp,
    GetMountedImageRsp,
    GetMouseJigglerRsp,
    GetOLEDRsp,
    GetPreviewRsp,
    GetSSHStateRsp,
    GetSwapSizeRsp,
    GetTailscaleStatusRsp,
    GetVersionRsp,
    GetVirtualDeviceRsp,
    GetWifiRsp,
    GpioType,
    HidMode,
    ImageEnabledRsp,
    IsPasswordUpdatedRsp,
    LoginReq,
    LoginRsp,
    MountImageReq,
    MouseJigglerMode,
    PasteReq,
    SetGpioReq,
    SetHidModeReq,
    SetMemoryLimitReq,
    SetMouseJigglerReq,
    SetOledReq,
    SetPreviewReq,
    SetSwapSizeReq,
    StatusImageRsp,
    UpdateVirtualDeviceReq,
    UpdateVirtualDeviceRsp,
    VirtualDevice,
    WakeOnLANReq,
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


class NanoKVMClient:
    """Async API client for the NanoKVM."""

    def __init__(
        self,
        url: str,
        session: ClientSession,
        *,
        token: str | None = None,
        request_timeout: int = 10,
    ) -> None:
        """Initialize the NanoKVM client."""
        self.url = yarl.URL(url)
        self.session = session
        self._token = token
        self._request_timeout = request_timeout

    @property
    def token(self) -> str | None:
        """Return the current auth token."""
        return self._token

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

        async with self.session.request(
            method,
            self.url / path.lstrip("/"),
            headers={
                hdrs.ACCEPT: "application/json",
            },
            cookies=cookies,
            timeout=aiohttp.ClientTimeout(total=self._request_timeout),
            raise_for_status=True,
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
            json=(data.dict() if data is not None else None),
            **kwargs,
        ) as response:
            try:
                raw_response = await response.json(content_type=None)
                _LOGGER.debug("Raw JSON response data: %s", raw_response)
                # Parse the outer ApiResponse structure
                api_response = ApiResponse[response_model].parse_obj(raw_response)  # type: ignore
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

    async def authenticate(self, username: str, password: str) -> None:
        """Authenticate and store the session token."""
        _LOGGER.debug("Attempting authentication for user: %s", username)
        try:
            login_response = await self._api_request_json(
                hdrs.METH_POST,
                "/auth/login",
                response_model=LoginRsp,
                authenticate=False,
                data=LoginReq(
                    username=username,
                    password=obfuscate_password(password),
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
        await self._api_request_json(
            hdrs.METH_POST,
            "/auth/password",
            data=ChangePasswordReq(
                username=username,
                password=obfuscate_password(new_password),
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

    async def get_gpio(self) -> GetGpioRsp:
        """Get GPIO LED status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/gpio",
            response_model=GetGpioRsp,
        )

    async def get_ssh_state(self) -> GetSSHStateRsp:
        """Get SSH enabled state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/ssh",
            response_model=GetSSHStateRsp,
        )

    async def get_swap_size(self) -> int:
        """Get Swap size."""
        rsp = await self._api_request_json(
            hdrs.METH_GET,
            "/vm/swap",
            response_model=GetSwapSizeRsp,
        )
        return rsp.size

    async def set_swap_size(self, size_mb: int) -> None:
        """Set the Swap size."""
        await self._api_request_json(
            hdrs.METH_POST, "/vm/swap", data=SetSwapSizeReq(size=size_mb)
        )

    async def get_mdns_state(self) -> GetMdnsStateRsp:
        """Get mDNS enabled state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/mdns",
            response_model=GetMdnsStateRsp,
        )

    async def get_hid_mode(self) -> GetHidModeRsp:
        """Get the current HID mode."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/hid/mode",
            response_model=GetHidModeRsp,
        )

    async def get_oled_info(self) -> GetOLEDRsp:
        """Get OLED information."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/oled",
            response_model=GetOLEDRsp,
        )

    async def get_virtual_device_status(self) -> GetVirtualDeviceRsp:
        """Get the status of virtual network/disk devices."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/device/virtual",
            response_model=GetVirtualDeviceRsp,
        )

    async def get_memory_limit(self) -> GetMemoryLimitRsp:
        """Get the configured Go memory limit."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/memory/limit",
            response_model=GetMemoryLimitRsp,
        )

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

    async def get_wifi_status(self) -> GetWifiRsp:
        """Get WiFi status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/network/wifi",
            response_model=GetWifiRsp,
        )

    async def get_tailscale_status(self) -> GetTailscaleStatusRsp:
        """Get Tailscale status."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/extensions/tailscale/status",
            response_model=GetTailscaleStatusRsp,
        )

    async def get_mounted_image(self) -> GetMountedImageRsp:
        """Get the currently mounted image file."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/storage/image/mounted",
            response_model=GetMountedImageRsp,
        )

    async def get_cdrom_status(self) -> GetCdRomRsp:
        """Check if the mounted image is in CD-ROM mode."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/storage/cdrom",
            response_model=GetCdRomRsp,
        )

    async def is_image_download_enabled(self) -> ImageEnabledRsp:
        """Check if the /data partition allows downloads."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/download/image/enabled",
            response_model=ImageEnabledRsp,
        )

    async def get_image_download_status(self) -> StatusImageRsp:
        """Get the status of an ongoing image download."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/download/image/status",
            response_model=StatusImageRsp,
        )

    async def push_button(self, button: GpioType, duration_ms: int) -> None:
        """Simulate pushing a hardware button."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/gpio",
            data=SetGpioReq(type=button, duration=duration_ms),
        )

    async def set_hid_mode(self, mode: HidMode) -> None:
        """Set the HID mode (requires reboot)."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/hid/mode",
            data=SetHidModeReq(mode=mode),
        )

    async def set_preview_state(self, enable: bool) -> None:
        """Enable or disable preview updates."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/application/preview",
            data=SetPreviewReq(enable=enable),
        )

    async def set_oled_sleep(self, sleep_seconds: int) -> None:
        """Set the OLED sleep timeout."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/oled",
            data=SetOledReq(sleep=sleep_seconds),
        )

    async def set_memory_limit(self, enabled: bool, limit_mb: int) -> None:
        """Set or disable the Go memory limit."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/vm/memory/limit",
            data=SetMemoryLimitReq(enabled=enabled, limit=limit_mb),
        )

    async def update_virtual_device(
        self, device: VirtualDevice
    ) -> UpdateVirtualDeviceRsp:
        """Toggle the state of a virtual device (network or disk)."""
        return await self._api_request_json(
            hdrs.METH_POST,
            "/vm/device/virtual",
            response_model=UpdateVirtualDeviceRsp,
            data=UpdateVirtualDeviceReq(device=device),
        )

    async def mount_image(self, file: str | None = None, cdrom: bool = False) -> None:
        """Mount an image file or unmount if file is None."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/storage/image/mount",
            data=MountImageReq(file=file, cdrom=cdrom if file else None),
        )

    async def download_image(self, url: str) -> StatusImageRsp:
        """Start downloading an image from a URL."""
        return await self._api_request_json(
            hdrs.METH_POST,
            "/download/image",
            response_model=StatusImageRsp,
            data=DownloadImageReq(file=url),
        )

    async def send_wake_on_lan(self, mac: str) -> None:
        """Send a Wake-on-LAN packet."""
        await self._api_request_json(
            hdrs.METH_POST, "/network/wol", data=WakeOnLANReq(mac=mac)
        )

    async def connect_wifi(self, ssid: str, password: str) -> None:
        """Attempt to connect to a WiFi network."""
        await self._api_request_json(
            hdrs.METH_POST,
            "/network/wifi",
            data=ConnectWifiReq(ssid=ssid, password=password),
        )

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

    async def enable_ssh(self) -> None:
        """Enable SSH server."""
        await self._api_request_json(hdrs.METH_POST, "/vm/ssh/enable")

    async def disable_ssh(self) -> None:
        """Disable SSH server."""
        await self._api_request_json(hdrs.METH_POST, "/vm/ssh/disable")

    async def enable_swap(self) -> None:
        """Enable swap."""
        await self._api_request_json(hdrs.METH_POST, "/vm/swap/enable")

    async def disable_swap(self) -> None:
        """Disable swap."""
        await self._api_request_json(hdrs.METH_POST, "/vm/swap/disable")

    async def enable_mdns(self) -> None:
        """Enable mDNS."""
        await self._api_request_json(hdrs.METH_POST, "/vm/mdns/enable")

    async def disable_mdns(self) -> None:
        """Disable mDNS."""
        await self._api_request_json(hdrs.METH_POST, "/vm/mdns/disable")

    async def reboot_system(self) -> None:
        """Reboot the KVM device."""
        await self._api_request_json(hdrs.METH_POST, "/vm/system/reboot")

    async def get_hdmi_state(self) -> GetHdmiStateRsp:
        """Get the HDMI state."""
        return await self._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi",
            response_model=GetHdmiStateRsp,
        )

    async def reset_hdmi(self) -> None:
        """Reset the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/reset")

    async def enable_hdmi(self) -> None:
        """Enable the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/enable")

    async def disable_hdmi(self) -> None:
        """Disable the HDMI connection."""
        await self._api_request_json(hdrs.METH_POST, "/vm/hdmi/disable")

    async def reset_hid(self) -> None:
        """Reset the HID subsystem."""
        await self._api_request_json(hdrs.METH_POST, "/hid/reset")

    async def update_application(self) -> None:
        """Trigger the application update process."""
        await self._api_request_json(hdrs.METH_POST, "/application/update")

    async def tailscale_install(self) -> None:
        """Perform a Tailscale action: install."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/install")

    async def tailscale_uninstall(self) -> None:
        """Perform a Tailscale action: uninstall."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/uninstall")

    async def tailscale_up(self) -> None:
        """Perform a Tailscale action: up."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/up")

    async def tailscale_down(self) -> None:
        """Perform a Tailscale action: down."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/down")

    async def tailscale_login(self) -> None:
        """Perform a Tailscale action: login."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/login")

    async def tailscale_logout(self) -> None:
        """Perform a Tailscale action: logout."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/logout")

    async def tailscale_start(self) -> None:
        """Perform a Tailscale action: start."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/start")

    async def tailscale_stop(self) -> None:
        """Perform a Tailscale action: stop."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/stop")

    async def tailscale_restart(self) -> None:
        """Perform a Tailscale action: restart."""
        await self._api_request_json(hdrs.METH_POST, "/extensions/tailscale/restart")

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
            "/vm/mouse-jiggler",
            data=SetMouseJigglerReq(enabled=enabled, mode=mode),
        )
