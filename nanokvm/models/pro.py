"""Pro-only models for NanoKVM API."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import WiFiInfo, _CaseInsensitiveStrEnum

__all__ = [
    "DeleteEdidReq",
    "DiskType",
    "EdidPreset",
    "EdidValue",
    "GetCustomEdidListRsp",
    "GetEdidRsp",
    "GetHdmiCaptureRsp",
    "GetHdmiPassthroughRsp",
    "GetKvmadminStatusRsp",
    "GetLcdTimeFormatRsp",
    "GetLedStripRsp",
    "GetLowPowerRsp",
    "GetMenuBarConfigRsp",
    "GetStaticIPRsp",
    "GetTimeStatusRsp",
    "GetTimeZoneRsp",
    "LcdTimeFormat",
    "RateControlMode",
    "RefreshVirtualDeviceReq",
    "ScanWifiRsp",
    "SetFpsReq",
    "SetGopReq",
    "SetHdmiCaptureReq",
    "SetHdmiPassthroughReq",
    "SetLcdTimeFormatReq",
    "SetLedStripReq",
    "SetLowPowerReq",
    "SetMenuBarConfigReq",
    "SetRateControlModeReq",
    "SetStaticIPReq",
    "SetStreamModeReq",
    "SetStreamQualityReq",
    "SetTimeZoneReq",
    "StreamMode",
    "SwitchEdidReq",
    "UploadEdidRsp",
]


class DiskType(StrEnum):
    """Virtual Disk types."""

    SDCARD = "sdcard"
    EMMC = "emmc"


class RateControlMode(StrEnum):
    """Stream rate control modes."""

    CBR = "cbr"
    VBR = "vbr"


class StreamMode(StrEnum):
    """Video stream modes."""

    MJPEG = "mjpeg"
    H264_WEBRTC = "h264-webrtc"
    H264_DIRECT = "h264-direct"
    H265_WEBRTC = "h265-webrtc"
    H265_DIRECT = "h265-direct"


class LcdTimeFormat(_CaseInsensitiveStrEnum):
    """LCD clock display formats."""

    TWELVE_HOUR = "12h"
    TWENTY_FOUR_HOUR = "24h"


class EdidPreset(StrEnum):
    """Built-in NanoKVM Pro EDID presets, named as Pro 1.2.16 names them.

    Pro 1.2.15 and older use other names, and only they have
    ``WQUXGA_3840X2400_30HZ``.
    """

    UHD_4K_30HZ = "nanokvm-pro-3840x2160-30"
    UHD_4K_39HZ = "nanokvm-pro-3840x2160-39"
    QHD_1440P_60HZ = "nanokvm-pro-2560x1440-60"
    FHD_1080P_60HZ = "nanokvm-pro-1920x1080-60"
    WQUXGA_3840X2400_30HZ = "E58-4K16-10"
    ULTRAWIDE_3440X1440_60HZ = "nanokvm-pro-3440x1440-60"


EdidValue = EdidPreset | str

# Pro 1.2.15 and older names of the presets that 1.2.16 renamed.
_LEGACY_EDID_NAMES: dict[EdidPreset, str] = {
    EdidPreset.UHD_4K_30HZ: "E18-4K30FPS",
    EdidPreset.UHD_4K_39HZ: "E48-4K39FPS",
    EdidPreset.QHD_1440P_60HZ: "E56-2K60FPS",
    EdidPreset.FHD_1080P_60HZ: "E54-1080P60FPS",
    EdidPreset.ULTRAWIDE_3440X1440_60HZ: "E63-Ultrawide",
}
_PRESETS_BY_LEGACY_NAME = {name: preset for preset, name in _LEGACY_EDID_NAMES.items()}


def _normalize_edid_value(value: Any) -> Any:
    """Return the preset for either name of a built-in EDID, else ``value``."""
    if isinstance(value, str):
        try:
            return EdidPreset(value)
        except ValueError:
            return _PRESETS_BY_LEGACY_NAME.get(value, value)
    return value


class RefreshVirtualDeviceReq(BaseModel):
    device: str


class GetLowPowerRsp(BaseModel):
    enabled: bool


class SetLowPowerReq(BaseModel):
    enable: bool


class GetEdidRsp(BaseModel):
    edid: EdidValue

    @field_validator("edid", mode="before")
    @classmethod
    def _normalize_edid(cls, value: Any) -> Any:
        return _normalize_edid_value(value)


class SwitchEdidReq(BaseModel):
    # The client picks the name the firmware uses, so it is sent unchanged.
    edid: EdidValue


class GetCustomEdidListRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    edid_list: list[str] = Field(default_factory=list, alias="edidList")

    @field_validator("edid_list", mode="before")
    @classmethod
    def _normalize_edid_list(cls, value: Any) -> Any:
        return [] if value is None else value


class DeleteEdidReq(BaseModel):
    edid: str


class UploadEdidRsp(BaseModel):
    file: str


class GetHdmiCaptureRsp(BaseModel):
    enabled: bool


class SetHdmiCaptureReq(BaseModel):
    enabled: bool


class GetHdmiPassthroughRsp(BaseModel):
    enabled: bool


class SetHdmiPassthroughReq(BaseModel):
    enabled: bool


class SetTimeZoneReq(BaseModel):
    timezone: str


class GetTimeZoneRsp(BaseModel):
    timezone: str


class GetTimeStatusRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    is_synchronized: bool = Field(alias="isSynchronized")
    last_sync_time: int = Field(alias="lastSyncTime")


class GetLcdTimeFormatRsp(BaseModel):
    format: LcdTimeFormat


class SetLcdTimeFormatReq(BaseModel):
    format: LcdTimeFormat


class GetMenuBarConfigRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    disabled_items: list[str] = Field(default_factory=list, alias="disabledItems")


class SetMenuBarConfigReq(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    disabled_items: list[str] = Field(alias="disabledItems")


class SetLedStripReq(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    on: bool
    horizontal_count: int = Field(alias="hor")
    vertical_count: int = Field(alias="ver")
    brightness: int


class GetLedStripRsp(BaseModel):
    on: bool
    horizontal_count: int = Field(alias="hor")
    vertical_count: int = Field(alias="ver")
    brightness: int


# Network Models
class ScanWifiRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    wifi_list: list[WiFiInfo] = Field(default_factory=list, alias="wifiList")

    @field_validator("wifi_list", mode="before")
    @classmethod
    def _normalize_wifi_list(cls, value: Any) -> Any:
        return [] if value is None else value


class GetStaticIPRsp(BaseModel):
    enabled: bool
    ip: str


class SetStaticIPReq(BaseModel):
    enabled: bool
    ip: str


# Stream Models
class SetRateControlModeReq(BaseModel):
    mode: RateControlMode


class SetStreamModeReq(BaseModel):
    mode: StreamMode


class SetStreamQualityReq(BaseModel):
    quality: int


class SetGopReq(BaseModel):
    gop: int


class SetFpsReq(BaseModel):
    fps: int


# Extensions Models
class GetKvmadminStatusRsp(BaseModel):
    state: str
