"""Non-Pro-only models for NanoKVM API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScreenSettingType(StrEnum):
    """Screen Setting types."""

    RESOLUTION = "resolution"
    FPS = "fps"
    QUALITY = "quality"


class DNSMode(StrEnum):
    """DNS configuration modes."""

    MANUAL = "manual"
    DHCP = "dhcp"


class AIControlMode(StrEnum):
    """Exclusive control modes for MCP and PicoClaw."""

    OFF = "off"
    MCP = "mcp"
    PICOCLAW = "picoclaw"


class InputRegionMode(StrEnum):
    """Input region operation modes."""

    OFF = "off"
    AUTO = "auto"
    MANUAL = "manual"


def _normalize_string_list(value: Any) -> Any:
    if value is None:
        return []
    return value


class SetScreenReq(BaseModel):
    """Pro uses separate stream endpoints instead."""

    type: ScreenSettingType
    value: int


class GetMCPConfigRsp(BaseModel):
    """MCP configuration and current coordinated-control state."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    api_key: str = Field(alias="apiKey", repr=False)
    control_mode: AIControlMode = Field(alias="controlMode")
    transitioning: bool


class SetMCPConfigReq(BaseModel):
    """Enable or disable MCP control."""

    enabled: bool


class AIControlStatusRsp(BaseModel):
    """Current owner and transition state of coordinated input control."""

    mode: AIControlMode
    transitioning: bool
    last_error: str | None = None
    changed_at: datetime | None = None


class SetAIControlModeReq(BaseModel):
    """Select the owner of coordinated input control."""

    mode: AIControlMode


class SetAIControlModeRsp(AIControlStatusRsp):
    """Normalized result of a coordinated-control mode switch."""

    runtime: dict[str, Any] | None = None
    released: bool | None = None
    closed_sessions: int | None = None
    cleanup_warning: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _unwrap_control_status(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("control"), dict):
            return value

        return {
            **value["control"],
            **{key: item for key, item in value.items() if key != "control"},
        }


class OriginalResolution(BaseModel):
    """Resolution detected from the original display signal."""

    width: int
    height: int


class ManualRegion(BaseModel):
    """A configured manual region relative to its source frame."""

    model_config = ConfigDict(populate_by_name=True)

    frame_width: int = Field(alias="frameWidth")
    frame_height: int = Field(alias="frameHeight")
    left: int
    top: int
    width: int
    height: int


class GetInputRegionRsp(BaseModel):
    """Current input region configuration."""

    model_config = ConfigDict(populate_by_name=True)

    mode: InputRegionMode
    frame_width: int = Field(default=0, alias="frameWidth")
    frame_height: int = Field(default=0, alias="frameHeight")
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    resolutions: list[OriginalResolution] = Field(default_factory=list)
    selected_resolution: str = Field(default="", alias="selectedResolution")
    regions: list[ManualRegion] = Field(default_factory=list)
    selected_region: str = Field(default="", alias="selectedRegion")


class SetInputRegionReq(BaseModel):
    """Input region configuration fields to update."""

    model_config = ConfigDict(populate_by_name=True)

    mode: InputRegionMode
    frame_width: int | None = Field(default=None, alias="frameWidth")
    frame_height: int | None = Field(default=None, alias="frameHeight")
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None
    resolutions: list[OriginalResolution] | None = None
    selected_resolution: str | None = Field(default=None, alias="selectedResolution")
    regions: list[ManualRegion] | None = None
    selected_region: str | None = Field(default=None, alias="selectedRegion")


class GetInputResolutionRsp(BaseModel):
    """Current source display resolution."""

    width: int
    height: int


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
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    signal: bool | None = None
    idle_timeout: int | None = Field(default=None, alias="idleTimeout")


class SetHdmiIdleTimeoutReq(BaseModel):
    minutes: int


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
