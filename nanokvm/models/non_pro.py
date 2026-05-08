"""Non-Pro-only models for NanoKVM API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ScreenSettingType(StrEnum):
    """Screen Setting types."""

    RESOLUTION = "resolution"
    FPS = "fps"
    QUALITY = "quality"


class SetScreenReq(BaseModel):
    """Pro uses separate stream endpoints instead."""

    type: ScreenSettingType
    value: int


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
