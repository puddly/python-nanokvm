"""Models for NanoKVM API."""

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


class ScreenSettingType(StrEnum):
    """Screen Setting types (non-Pro only)."""

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
    MIC = "mic"  # Pro only


class DiskType(StrEnum):
    """Virtual Disk types (Pro only)."""

    SDCARD = "sdcard"  # Pro only
    EMMC = "emmc"  # Pro only


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


class RateControlMode(StrEnum):
    """Stream rate control modes (Pro only)."""

    CBR = "cbr"  # Pro only
    VBR = "vbr"  # Pro only


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


class SetScreenReq(BaseModel):
    """Non-Pro only. Pro uses separate stream endpoints."""

    type: ScreenSettingType
    value: int


class GetScriptsRsp(BaseModel):
    files: list[str]


class RunScriptReq(BaseModel):
    name: str
    type: RunScriptType


class RunScriptRsp(BaseModel):
    log: str


class DeleteScriptReq(BaseModel):
    name: str


class GetVirtualDeviceRsp(BaseModel):
    """Non-Pro virtual device status."""

    network: bool
    media: bool
    disk: bool


class GetVirtualDeviceProRsp(BaseModel):
    """Pro virtual device status."""

    model_config = ConfigDict(populate_by_name=True)

    is_network_enabled: bool = Field(alias="isNetworkEnabled")
    is_mic_enabled: bool = Field(alias="isMicEnabled")
    mounted_disk: str = Field(alias="mountedDisk")
    is_sd_card_exist: bool = Field(alias="isSdCardExist")
    is_emmc_exist: bool = Field(alias="isEmmcExist")


class UpdateVirtualDeviceReq(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device: VirtualDevice
    type: str | None = None  # Pro only (sdcard/emmc for disk device)


class RefreshVirtualDeviceReq(BaseModel):
    """Pro only."""

    device: str


class GetMemoryLimitRsp(BaseModel):
    """Non-Pro only."""

    enabled: bool
    limit: int  # In MB


class SetMemoryLimitReq(BaseModel):
    """Non-Pro only."""

    enabled: bool
    limit: int  # In MB


class GetOLEDRsp(BaseModel):
    exist: bool
    type: str = ""  # Pro only
    sleep: int  # Sleep timeout in seconds


class SetOledReq(BaseModel):
    sleep: int  # Sleep timeout in seconds


class GetSSHStateRsp(BaseModel):
    enabled: bool


class GetSwapSizeRsp(BaseModel):
    """Non-Pro only."""

    size: int


class SetSwapSizeReq(BaseModel):
    """Non-Pro only."""

    size: int


class GetMdnsStateRsp(BaseModel):
    enabled: bool


class GetMouseJigglerRsp(BaseModel):
    enabled: bool
    mode: MouseJigglerMode


class SetMouseJigglerReq(BaseModel):
    enabled: bool
    mode: MouseJigglerMode


class GetHdmiStateRsp(BaseModel):
    """Non-Pro only (PCIe variant)."""

    enabled: bool


class GetWebTitleRsp(BaseModel):
    title: str


class SetWebTitleReq(BaseModel):
    title: str


# Pro-only VM Models
class GetLowPowerRsp(BaseModel):
    """Pro only."""

    enabled: bool


class SetLowPowerReq(BaseModel):
    """Pro only."""

    enable: bool


class GetEdidRsp(BaseModel):
    """Pro only."""

    edid: str


class SwitchEdidReq(BaseModel):
    """Pro only."""

    edid: str


class GetCustomEdidListRsp(BaseModel):
    """Pro only."""

    model_config = ConfigDict(populate_by_name=True)

    edid_list: list[str] = Field(default_factory=list, alias="edidList")


class DeleteEdidReq(BaseModel):
    """Pro only."""

    edid: str


class GetHdmiCaptureRsp(BaseModel):
    """Pro only."""

    enabled: bool


class SetHdmiCaptureReq(BaseModel):
    """Pro only."""

    enabled: bool


class GetHdmiPassthroughRsp(BaseModel):
    """Pro only."""

    enabled: bool


class SetHdmiPassthroughReq(BaseModel):
    """Pro only."""

    enabled: bool


class SetTimeZoneReq(BaseModel):
    """Pro only."""

    timezone: str


class GetTimeZoneRsp(BaseModel):
    """Pro only."""

    timezone: str


class GetTimeStatusRsp(BaseModel):
    """Pro only."""

    model_config = ConfigDict(populate_by_name=True)

    is_synchronized: bool = Field(alias="isSynchronized")
    last_sync_time: int = Field(alias="lastSyncTime")


class GetLcdTimeFormatRsp(BaseModel):
    """Pro only."""

    format: str


class SetLcdTimeFormatReq(BaseModel):
    """Pro only."""

    format: str


class GetMenuBarConfigRsp(BaseModel):
    """Pro only."""

    model_config = ConfigDict(populate_by_name=True)

    disabled_items: list[str] = Field(default_factory=list, alias="disabledItems")


class SetMenuBarConfigReq(BaseModel):
    """Pro only."""

    model_config = ConfigDict(populate_by_name=True)

    disabled_items: list[str] = Field(alias="disabledItems")


class SetLedStripReq(BaseModel):
    """Pro only."""

    model_config = ConfigDict(populate_by_name=True)

    on: bool
    horizontal_count: int = Field(alias="hor")
    vertical_count: int = Field(alias="ver")
    brightness: int


class GetLedStripRsp(BaseModel):
    """Pro only."""

    on: bool
    horizontal_count: int = Field(alias="hor")
    vertical_count: int = Field(alias="ver")
    brightness: int


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


class GetCdRomRsp(BaseModel):
    """Non-Pro only."""

    cdrom: int


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
    """WiFi connection details (Pro only)."""

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


class ScanWifiRsp(BaseModel):
    """Pro only."""

    model_config = ConfigDict(populate_by_name=True)

    wifi_list: list[WiFiInfo] = Field(default_factory=list, alias="wifiList")


class ConnectWifiReq(BaseModel):
    ssid: str
    password: str


class GetStaticIPRsp(BaseModel):
    """Pro only."""

    enabled: bool
    ip: str


class SetStaticIPReq(BaseModel):
    """Pro only."""

    enabled: bool
    ip: str


class GetTailscaleStatusRsp(BaseModel):
    state: TailscaleState
    name: str
    ip: str
    account: str


class LoginTailscaleRsp(BaseModel):
    url: str


# Stream Models (Pro only)
class SetRateControlModeReq(BaseModel):
    """Pro only."""

    mode: RateControlMode


class SetStreamModeReq(BaseModel):
    """Pro only."""

    mode: str


class SetStreamQualityReq(BaseModel):
    """Pro only."""

    quality: int


class SetGopReq(BaseModel):
    """Pro only."""

    gop: int


class SetFpsReq(BaseModel):
    """Pro only."""

    fps: int


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


# Extensions Models (Pro only)
class GetKvmadminStatusRsp(BaseModel):
    """Pro only."""

    state: str
