"""Shared models for NanoKVM API (both Pro and non-Pro)."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

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


class RunScriptType(StrEnum):
    """Script Execution types."""

    FOREGROUND = "foreground"
    BACKGROUND = "background"


class VirtualDevice(StrEnum):
    """Virtual Device types."""

    NETWORK = "network"
    DISK = "disk"
    MIC = "mic"  # Pro only


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
    PRO = "Pro"
    UNKNOWN = "Unknown"


class MouseJigglerMode(StrEnum):
    """Mouse Jiggler Modes."""

    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class MouseButton(IntEnum):
    """Mouse Button types."""

    LEFT = 1
    RIGHT = 2
    MIDDLE = 4


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
    count: int | None = None  # Pro only


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
    part_number: str = Field("", alias="pn")  # Pro only
    arch: str = ""  # Pro only


class GetHostnameRsp(BaseModel):
    hostname: str


class SetHostnameReq(BaseModel):
    hostname: str  # Applies after reboot


class GetHardwareRsp(BaseModel):
    version: HWVersion


class SetGpioReq(BaseModel):
    type: GpioType
    duration: int  # Milliseconds


class GetGpioRsp(BaseModel):
    pwr: bool  # Power LED state
    hdd: bool  # HDD LED state (only valid for Alpha hardware)


class GetScriptsRsp(BaseModel):
    files: list[str]


class RunScriptReq(BaseModel):
    name: str
    type: RunScriptType


class RunScriptRsp(BaseModel):
    log: str


class DeleteScriptReq(BaseModel):
    name: str


class UpdateVirtualDeviceReq(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device: VirtualDevice
    type: str | None = None  # Pro only (sdcard/emmc for disk device)


class GetOLEDRsp(BaseModel):
    exist: bool
    type: str = ""  # Pro only
    sleep: int  # Sleep timeout in seconds


class SetOledReq(BaseModel):
    sleep: int  # Sleep timeout in seconds


class GetSSHStateRsp(BaseModel):
    enabled: bool


class GetMdnsStateRsp(BaseModel):
    enabled: bool


class GetMouseJigglerRsp(BaseModel):
    enabled: bool
    mode: MouseJigglerMode


class SetMouseJigglerReq(BaseModel):
    enabled: bool
    mode: MouseJigglerMode


class GetWebTitleRsp(BaseModel):
    title: str


class SetWebTitleReq(BaseModel):
    title: str


# HID Models
class GetHidModeRsp(BaseModel):
    mode: HidMode


class SetHidModeReq(BaseModel):
    mode: HidMode


class PasteReq(BaseModel):
    content: str


class ShortcutKey(BaseModel):
    code: str
    label: str


class Shortcut(BaseModel):
    id: str
    keys: list[ShortcutKey]


class GetShortcutsRsp(BaseModel):
    shortcuts: list[Shortcut]


class AddShortcutReq(BaseModel):
    keys: list[ShortcutKey]


class DeleteShortcutReq(BaseModel):
    id: str


class GetLeaderKeyRsp(BaseModel):
    key: str


class SetLeaderKeyReq(BaseModel):
    key: str


# Storage Models
class GetImagesRsp(BaseModel):
    files: list[str]


class MountImageReq(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file: str | None = None
    cdrom: bool | None = None
    read_only: bool | None = Field(None, alias="readOnly")  # Pro only


class GetMountedImageRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file: str  # Path to the mounted file, empty if none or default
    cdrom: bool = False  # Pro only
    read_only: bool = Field(False, alias="readOnly")  # Pro only


class DeleteImageReq(BaseModel):
    file: str


# Network Models
class WakeOnLANReq(BaseModel):
    mac: str


class GetMacRsp(BaseModel):
    macs: list[str]


class DeleteMacReq(BaseModel):
    mac: str


class SetMacNameReq(BaseModel):
    mac: str
    name: str


class WiFiInfo(BaseModel):
    """WiFi connection details."""

    ssid: str
    bssid: str
    signal: int
    frequency: int
    security: str


class GetWifiRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    supported: bool
    ap_mode: bool = Field(False, alias="apMode")
    connected: bool
    ssid: str = ""  # Non-Pro only
    wifi: WiFiInfo | None = None  # Pro only


class ConnectWifiReq(BaseModel):
    ssid: str
    password: str


class GetTailscaleStatusRsp(BaseModel):
    state: TailscaleState
    name: str
    ip: str
    account: str


class LoginTailscaleRsp(BaseModel):
    url: str


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
