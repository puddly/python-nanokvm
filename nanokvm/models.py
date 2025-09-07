"""Models for NanoKVM API."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponseCode(IntEnum):
    """API Response Codes."""

    SUCCESS = 0
    FAILURE = -1
    INVALID_USERNAME_OR_PASSWORD = -2


class HidMode(StrEnum):
    """HID Operating Modes."""

    NORMAL = "normal"
    HID_ONLY = "hid-only"


class GpioType(StrEnum):
    """GPIO Control types."""

    RESET = "reset"
    POWER = "power"


class ScreenSettingType(StrEnum):
    """Screen Setting types."""

    RESOLUTION = "resolution"
    FPS = "fps"
    QUALITY = "quality"


class RunScriptType(StrEnum):
    """Script Execution types."""

    FOREGROUND = "foreground"
    BACKGROUND = "background"


class VirtualDevice(StrEnum):
    """Virtual Device types."""

    NETWORK = "network"
    DISK = "disk"


class TailscaleState(StrEnum):
    """Tailscale Service states."""

    NOT_INSTALLED = "notInstall"
    NOT_RUNNING = "notRunning"
    NOT_LOGIN = "notLogin"
    STOPPED = "stopped"
    RUNNING = "running"


class TailscaleAction(StrEnum):
    """Tailscale Service action."""

    INSTALL = "install"
    UNINSTALL = "uninstall"
    UP = "up"
    DOWN = "down"
    LOGIN = "login"
    LOGOUT = "logout"
    START = "start"
    STOP = "stop"
    RESTART = "restart"


class DownloadStatus(StrEnum):
    """Download Status."""

    IDLE = "idle"
    IN_PROGRESS = "in_progress"


class HWVersion(StrEnum):
    """Hardware Version Enum based on Go constants."""

    ALPHA = "Alpha"
    BETA = "Beta"
    PCIE = "PCIE"
    UNKNOWN = "Unknown"


class MouseJigglerMode(StrEnum):
    """Mouse Jiggler Modes."""

    ABSOLUTE = "absolute"
    RELATIVE = "relative"


# Generic Response Wrapper
class ApiResponse(BaseModel, Generic[T]):
    """Generic API response structure."""

    code: ApiResponseCode
    msg: str
    data: T | None = None


# Authentication Models
class LoginReq(BaseModel):
    username: str
    password: str


class LoginRsp(BaseModel):
    token: str


class GetAccountRsp(BaseModel):
    username: str


class ChangePasswordReq(BaseModel):
    username: str
    password: str


class IsPasswordUpdatedRsp(BaseModel):
    is_updated: bool = Field(alias="isUpdated")


# VM Models
class IPInfo(BaseModel):
    """IP Address Information."""

    name: str
    addr: str
    version: str
    type: str


class GetInfoRsp(BaseModel):
    ips: list[IPInfo]
    mdns: str
    image: str
    application: str
    device_key: str = Field(alias="deviceKey")


class GetHardwareRsp(BaseModel):
    version: HWVersion


class SetGpioReq(BaseModel):
    type: GpioType
    duration: int  # Milliseconds


class GetGpioRsp(BaseModel):
    pwr: bool  # Power LED state
    hdd: bool  # HDD LED state (only valid for Alpha hardware)


class SetScreenReq(BaseModel):
    type: ScreenSettingType
    value: int


class GetVirtualDeviceRsp(BaseModel):
    network: bool
    disk: bool


class UpdateVirtualDeviceReq(BaseModel):
    device: VirtualDevice


class UpdateVirtualDeviceRsp(BaseModel):
    on: bool


class GetMemoryLimitRsp(BaseModel):
    enabled: bool
    limit: int  # In MB


class SetMemoryLimitReq(BaseModel):
    enabled: bool
    limit: int  # In MB


class GetOLEDRsp(BaseModel):
    exist: bool
    sleep: int  # Sleep timeout in seconds


class SetOledReq(BaseModel):
    sleep: int  # Sleep timeout in seconds


class GetSSHStateRsp(BaseModel):
    enabled: bool


class GetSwapSizeRsp(BaseModel):
    size: int


class SetSwapSizeReq(BaseModel):
    size: int


class GetMdnsStateRsp(BaseModel):
    enabled: bool


# HID Models
class GetHidModeRsp(BaseModel):
    mode: HidMode


class SetHidModeReq(BaseModel):
    mode: HidMode


class PasteReq(BaseModel):
    content: str


# Storage Models
class GetImagesRsp(BaseModel):
    files: list[str]


class MountImageReq(BaseModel):
    file: str | None = None
    cdrom: bool | None = None


class GetMountedImageRsp(BaseModel):
    file: str  # Path to the mounted file, empty if none or default


class GetCdRomRsp(BaseModel):
    cdrom: int


# Network Models
class WakeOnLANReq(BaseModel):
    mac: str


class GetMacRsp(BaseModel):
    macs: list[str]


class DeleteMacReq(BaseModel):
    mac: str


class GetWifiRsp(BaseModel):
    supported: bool
    connected: bool


class ConnectWifiReq(BaseModel):
    ssid: str
    password: str  # Plain text password


class GetTailscaleStatusRsp(BaseModel):
    state: TailscaleState
    name: str
    ip: str
    account: str


# Application Models
class GetVersionRsp(BaseModel):
    current: str
    latest: str


class GetPreviewRsp(BaseModel):
    enabled: bool


class SetPreviewReq(BaseModel):
    enable: bool


# Download Models
class ImageEnabledRsp(BaseModel):
    enabled: bool


class StatusImageRsp(BaseModel):
    status: DownloadStatus
    file: str
    percentage: str


class DownloadImageReq(BaseModel):
    file: str  # URL of the image to download
    # cdrom field is ignored for downloads


class SetMouseJigglerReq(BaseModel):
    enabled: bool
    mode: MouseJigglerMode


class GetMouseJigglerRsp(BaseModel):
    enabled: bool
    mode: MouseJigglerMode


class GetHdmiStateRsp(BaseModel):
    enabled: bool
