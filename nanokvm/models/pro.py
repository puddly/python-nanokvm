"""Pro-only models for NanoKVM API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .common import WiFiInfo


class DiskType(StrEnum):
    """Virtual Disk types."""

    SDCARD = "sdcard"
    EMMC = "emmc"


class RateControlMode(StrEnum):
    """Stream rate control modes."""

    CBR = "cbr"
    VBR = "vbr"


# VM Models
class GetVirtualDeviceProRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    is_network_enabled: bool = Field(alias="isNetworkEnabled")
    is_mic_enabled: bool = Field(alias="isMicEnabled")
    mounted_disk: str = Field(alias="mountedDisk")
    is_sd_card_exist: bool = Field(alias="isSdCardExist")
    is_emmc_exist: bool = Field(alias="isEmmcExist")


class RefreshVirtualDeviceReq(BaseModel):
    device: str


class GetLowPowerRsp(BaseModel):
    enabled: bool


class SetLowPowerReq(BaseModel):
    enable: bool


class GetEdidRsp(BaseModel):
    edid: str


class SwitchEdidReq(BaseModel):
    edid: str


class GetCustomEdidListRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    edid_list: list[str] = Field(default_factory=list, alias="edidList")


class DeleteEdidReq(BaseModel):
    edid: str


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
    format: str


class SetLcdTimeFormatReq(BaseModel):
    format: str


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
    mode: str


class SetStreamQualityReq(BaseModel):
    quality: int


class SetGopReq(BaseModel):
    gop: int


class SetFpsReq(BaseModel):
    fps: int


# Extensions Models
class GetKvmadminStatusRsp(BaseModel):
    state: str
