"""Non-Pro-only models for NanoKVM API."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScreenSettingType(StrEnum):
    """Screen Setting types."""

    RESOLUTION = "resolution"
    FPS = "fps"
    QUALITY = "quality"


class DNSMode(StrEnum):
    """DNS configuration modes."""

    MANUAL = "manual"
    DHCP = "dhcp"


def _normalize_string_list(value: Any) -> Any:
    if value is None:
        return []
    return value


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


class DNSInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    interface: str = ""
    type: str = ""
    address: str = ""
    subnet_mask: str = Field("", alias="subnetMask")
    gateway: str = ""
    search_domains: list[str] = Field(default_factory=list, alias="searchDomains")

    @field_validator("search_domains", mode="before")
    @classmethod
    def _normalize_search_domains(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class GetDNSRsp(BaseModel):
    mode: DNSMode
    servers: list[str] = Field(default_factory=list)
    effective: list[str] = Field(default_factory=list)
    dhcp: list[str] = Field(default_factory=list)
    info: DNSInfo

    @field_validator("servers", "effective", "dhcp", mode="before")
    @classmethod
    def _normalize_dns_lists(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class SetDNSReq(BaseModel):
    mode: DNSMode
    servers: list[str] = Field(default_factory=list)
