"""Shared models for NanoKVM API (both Pro and non-Pro)."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, IntFlag, StrEnum
from typing import Any, Generic, Self, TypeVar

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

__all__ = [
    "AddShortcutReq",
    "ApiResponse",
    "ApiResponseCode",
    "ChangePasswordReq",
    "ChangePasswordV251Req",
    "ConnectWifiReq",
    "DeleteImageReq",
    "DeleteMacReq",
    "DeleteScriptReq",
    "DeleteShortcutReq",
    "DownloadImageReq",
    "DownloadStatus",
    "GetAccountRsp",
    "GetGpioRsp",
    "GetHardwareRsp",
    "GetHidModeRsp",
    "GetHostnameRsp",
    "GetImagesRsp",
    "GetInfoRsp",
    "GetKeyboardLedStatusRsp",
    "GetLeaderKeyRsp",
    "GetMacRsp",
    "GetMdnsStateRsp",
    "GetMountedImageRsp",
    "GetMouseJigglerRsp",
    "GetOLEDRsp",
    "GetPreviewRsp",
    "GetSSHStateRsp",
    "GetScriptsRsp",
    "GetShortcutsRsp",
    "GetTailscaleStatusRsp",
    "GetVersionRsp",
    "GetVirtualDeviceRsp",
    "GetWebTitleRsp",
    "GetWifiRsp",
    "GpioType",
    "HWFamily",
    "HWVersion",
    "HidMode",
    "IPInfo",
    "ImageEnabledRsp",
    "ImageTransferProgress",
    "IsPasswordUpdatedRsp",
    "KeyCode",
    "KeyModifier",
    "LoginReq",
    "LoginRsp",
    "LoginTailscaleRsp",
    "MountImageReq",
    "MouseButton",
    "MouseJigglerMode",
    "OledType",
    "PasteReq",
    "RunScriptReq",
    "RunScriptRsp",
    "RunScriptType",
    "SetGpioReq",
    "SetHidModeReq",
    "SetHostnameReq",
    "SetLeaderKeyReq",
    "SetMacNameReq",
    "SetMouseJigglerReq",
    "SetOledReq",
    "SetPreviewReq",
    "SetWebTitleReq",
    "Shortcut",
    "ShortcutKey",
    "StatusImageRsp",
    "TailscaleState",
    "UpdateVirtualDeviceReq",
    "UploadScriptRsp",
    "VirtualDevice",
    "WakeOnLANReq",
    "WiFiInfo",
]

T = TypeVar("T")


class ApiResponseCode(IntEnum):
    """Documented shared API response codes."""

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


class DownloadStatus(StrEnum):
    """Download Status."""

    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    CHECKSUM_FAILED = "checksum_failed"


class HWFamily(StrEnum):
    """Hardware families supported by NanoKVM."""

    NON_PRO = "non-Pro"
    PRO = "Pro"


class HWVersion(StrEnum):
    """Hardware Version Enum based on Go constants."""

    ALPHA = "Alpha"
    BETA = "Beta"
    PCIE = "PCIE"
    PRO = "Pro"
    UNKNOWN = "Unknown"

    @property
    def family(self) -> HWFamily | None:
        """Return the product family for this exact hardware version."""
        if self is HWVersion.PRO:
            return HWFamily.PRO
        if self in (HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE):
            return HWFamily.NON_PRO
        return None


class MouseJigglerMode(StrEnum):
    """Mouse Jiggler Modes."""

    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class MouseButton(IntEnum):
    """Mouse Button types."""

    LEFT = 1
    RIGHT = 2
    MIDDLE = 4
    BACK = 8
    FORWARD = 16


class KeyCode(IntEnum):
    """USB HID keyboard usage codes, named after the browser key codes.

    Any other usage code can be passed as an ``int``.
    """

    KEY_A = 0x04
    KEY_B = 0x05
    KEY_C = 0x06
    KEY_D = 0x07
    KEY_E = 0x08
    KEY_F = 0x09
    KEY_G = 0x0A
    KEY_H = 0x0B
    KEY_I = 0x0C
    KEY_J = 0x0D
    KEY_K = 0x0E
    KEY_L = 0x0F
    KEY_M = 0x10
    KEY_N = 0x11
    KEY_O = 0x12
    KEY_P = 0x13
    KEY_Q = 0x14
    KEY_R = 0x15
    KEY_S = 0x16
    KEY_T = 0x17
    KEY_U = 0x18
    KEY_V = 0x19
    KEY_W = 0x1A
    KEY_X = 0x1B
    KEY_Y = 0x1C
    KEY_Z = 0x1D
    DIGIT_1 = 0x1E
    DIGIT_2 = 0x1F
    DIGIT_3 = 0x20
    DIGIT_4 = 0x21
    DIGIT_5 = 0x22
    DIGIT_6 = 0x23
    DIGIT_7 = 0x24
    DIGIT_8 = 0x25
    DIGIT_9 = 0x26
    DIGIT_0 = 0x27
    ENTER = 0x28
    ESCAPE = 0x29
    BACKSPACE = 0x2A
    TAB = 0x2B
    SPACE = 0x2C
    MINUS = 0x2D
    EQUAL = 0x2E
    BRACKET_LEFT = 0x2F
    BRACKET_RIGHT = 0x30
    BACKSLASH = 0x31
    SEMICOLON = 0x33
    QUOTE = 0x34
    BACKQUOTE = 0x35
    COMMA = 0x36
    PERIOD = 0x37
    SLASH = 0x38
    CAPS_LOCK = 0x39
    F1 = 0x3A
    F2 = 0x3B
    F3 = 0x3C
    F4 = 0x3D
    F5 = 0x3E
    F6 = 0x3F
    F7 = 0x40
    F8 = 0x41
    F9 = 0x42
    F10 = 0x43
    F11 = 0x44
    F12 = 0x45
    PRINT_SCREEN = 0x46
    SCROLL_LOCK = 0x47
    PAUSE = 0x48
    INSERT = 0x49
    HOME = 0x4A
    PAGE_UP = 0x4B
    DELETE = 0x4C
    END = 0x4D
    PAGE_DOWN = 0x4E
    ARROW_RIGHT = 0x4F
    ARROW_LEFT = 0x50
    ARROW_DOWN = 0x51
    ARROW_UP = 0x52
    NUM_LOCK = 0x53
    NUMPAD_DIVIDE = 0x54
    NUMPAD_MULTIPLY = 0x55
    NUMPAD_SUBTRACT = 0x56
    NUMPAD_ADD = 0x57
    NUMPAD_ENTER = 0x58
    NUMPAD_1 = 0x59
    NUMPAD_2 = 0x5A
    NUMPAD_3 = 0x5B
    NUMPAD_4 = 0x5C
    NUMPAD_5 = 0x5D
    NUMPAD_6 = 0x5E
    NUMPAD_7 = 0x5F
    NUMPAD_8 = 0x60
    NUMPAD_9 = 0x61
    NUMPAD_0 = 0x62
    NUMPAD_DECIMAL = 0x63
    INTL_BACKSLASH = 0x64
    CONTEXT_MENU = 0x65
    POWER = 0x66
    NUMPAD_EQUAL = 0x67
    F13 = 0x68
    F14 = 0x69
    F15 = 0x6A
    F16 = 0x6B
    F17 = 0x6C
    F18 = 0x6D
    F19 = 0x6E
    F20 = 0x6F
    F21 = 0x70
    F22 = 0x71
    F23 = 0x72
    F24 = 0x73


class KeyModifier(IntFlag):
    """Modifier key bits of a keyboard report."""

    LEFT_CTRL = 0x01
    LEFT_SHIFT = 0x02
    LEFT_ALT = 0x04
    LEFT_META = 0x08
    RIGHT_CTRL = 0x10
    RIGHT_SHIFT = 0x20
    RIGHT_ALT = 0x40
    RIGHT_META = 0x80


class _CaseInsensitiveStrEnum(StrEnum):
    """String enum with case-insensitive parsing."""

    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        if isinstance(value, str):
            normalized = value.lower()
            for member in cls:
                if member.value.lower() == normalized:
                    return member
        return None


class OledType(_CaseInsensitiveStrEnum):
    """OLED variants used by NanoKVM Pro."""

    ATX = "ATX"
    DESK = "DESK"


# Generic Response Wrapper
class ApiResponse(BaseModel, Generic[T]):
    """Generic API response structure."""

    code: int
    msg: str = Field(validation_alias=AliasChoices("msg", "message"))
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
    role: str | None = None


class ChangePasswordReq(BaseModel):
    username: str
    password: str


class ChangePasswordV251Req(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    current_password: str = Field(alias="currentPassword")
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
    part_number: str | None = Field(default=None, alias="pn")  # Pro only
    arch: str | None = None  # Pro only

    @field_validator("part_number", "arch", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


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
    files: list[str] = Field(default_factory=list)

    @field_validator("files", mode="before")
    @classmethod
    def _normalize_files(cls, value: Any) -> Any:
        return [] if value is None else value


class UploadScriptRsp(BaseModel):
    file: str


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


class GetVirtualDeviceRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    network: bool = False
    media: bool | None = None
    disk: bool | None = None
    mic: bool | None = None
    is_network_enabled: bool | None = Field(default=None, alias="isNetworkEnabled")
    is_mic_enabled: bool | None = Field(default=None, alias="isMicEnabled")
    mounted_disk: str | None = Field(default=None, alias="mountedDisk")
    is_sd_card_exist: bool | None = Field(default=None, alias="isSdCardExist")
    is_emmc_exist: bool | None = Field(default=None, alias="isEmmcExist")

    @model_validator(mode="before")
    @classmethod
    def _normalize_virtual_device_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)

        if "network" not in data and "isNetworkEnabled" in data:
            data["network"] = data["isNetworkEnabled"]

        if "mic" not in data and "isMicEnabled" in data:
            data["mic"] = data["isMicEnabled"]

        return data

    @field_validator("mounted_disk", mode="before")
    @classmethod
    def _normalize_mounted_disk(cls, value: Any) -> Any:
        if value == "":
            return None
        return value


class GetOLEDRsp(BaseModel):
    exist: bool
    type: OledType | None = None  # Pro only
    sleep: int  # Sleep timeout in seconds

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_oled_type(cls, value: Any) -> Any:
        if value == "":
            return None
        return value


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


class GetKeyboardLedStatusRsp(BaseModel):
    """Keyboard LED state reported by the host computer."""

    model_config = ConfigDict(populate_by_name=True)

    num_lock: bool = Field(alias="numLock")
    caps_lock: bool = Field(alias="capsLock")
    scroll_lock: bool = Field(alias="scrollLock")
    known: bool
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    @field_validator("updated_at", mode="before")
    @classmethod
    def _normalize_updated_at(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


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
    shortcuts: list[Shortcut] = Field(default_factory=list)

    @field_validator("shortcuts", mode="before")
    @classmethod
    def _normalize_shortcuts(cls, value: Any) -> Any:
        return [] if value is None else value


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
    files: list[str] = Field(default_factory=list)

    @field_validator("files", mode="before")
    @classmethod
    def _normalize_files(cls, value: Any) -> list[str]:
        return [] if value is None else value


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
    macs: list[str] = Field(default_factory=list)

    @field_validator("macs", mode="before")
    @classmethod
    def _normalize_macs(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [item for item in value if not isinstance(item, str) or item.strip()]
        return value


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

    @model_validator(mode="before")
    @classmethod
    def _normalize_wifi_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        wifi = data.get("wifi")
        if data.get("ssid") in (None, "") and isinstance(wifi, dict):
            data["ssid"] = wifi.get("ssid", "")

        return data


class ConnectWifiReq(BaseModel):
    ssid: str
    password: str = ""


class GetTailscaleStatusRsp(BaseModel):
    state: TailscaleState
    name: str
    ip: str
    account: str


class LoginTailscaleRsp(BaseModel):
    url: str = ""


# Application Models
class GetVersionRsp(BaseModel):
    current: str
    latest: str


class GetPreviewRsp(BaseModel):
    enabled: bool


class SetPreviewReq(BaseModel):
    enable: bool


# Download Models
class ImageTransferProgress(BaseModel):
    """Progress reported while uploading a local image file."""

    bytes_transferred: int
    total_bytes: int
    percentage: float


class ImageEnabledRsp(BaseModel):
    enabled: bool


class StatusImageRsp(BaseModel):
    status: DownloadStatus
    file: str
    percentage: str


class DownloadImageReq(BaseModel):
    file: str  # URL of the image to download
    sha256sum: str | None = None
