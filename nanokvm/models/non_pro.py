"""Non-Pro-only models for NanoKVM API."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScreenSettingType(StrEnum):
    """Screen Setting types."""

    RESOLUTION = "resolution"
    FPS = "fps"
    QUALITY = "quality"


class SetScreenReq(BaseModel):
    """Pro uses separate stream endpoints instead."""

    type: ScreenSettingType
    value: int


class GetVirtualDeviceRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    network: bool = False
    media: bool | None = None
    disk: bool = False
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

        if "media" not in data and "mountedDisk" in data:
            data["media"] = bool(data["mountedDisk"])

        if "disk" not in data and "mountedDisk" in data:
            data["disk"] = bool(data["mountedDisk"])

        return data

    @field_validator("mounted_disk", mode="before")
    @classmethod
    def _normalize_mounted_disk(cls, value: Any) -> Any:
        if value == "":
            return None
        return value


class GetMemoryLimitRsp(BaseModel):
    enabled: bool
    limit: int  # In MB


class SetMemoryLimitReq(BaseModel):
    enabled: bool
    limit: int  # In MB


class GetSwapSizeRsp(BaseModel):
    size: int


class SetSwapSizeReq(BaseModel):
    size: int


class GetHdmiStateRsp(BaseModel):
    enabled: bool


class GetCdRomRsp(BaseModel):
    cdrom: int
