"""Non-Pro-only models for NanoKVM API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AIControlMode",
    "AIControlStatusRsp",
    "CreateUserReq",
    "DNSInfo",
    "DNSMode",
    "EjectMediaReq",
    "GetAutostartRsp",
    "GetCdRomRsp",
    "GetDNSRsp",
    "GetHdmiStateRsp",
    "GetInputRegionRsp",
    "GetInputResolutionRsp",
    "GetMCPConfigRsp",
    "GetMediaFilesRsp",
    "GetMediaRsp",
    "GetMediaStateRsp",
    "GetMediaStatusRsp",
    "GetMemoryLimitRsp",
    "GetPermissionsRsp",
    "GetSwapSizeRsp",
    "GetUpdateServerRsp",
    "GetUsersRsp",
    "InputRegionMode",
    "ManualRegion",
    "MediaCapabilities",
    "MediaFile",
    "MediaItem",
    "MediaPathReq",
    "MediaRefReq",
    "MediaType",
    "MountMediaReq",
    "OriginalResolution",
    "Permission",
    "PermissionInfo",
    "ScreenSettingType",
    "SetAIControlModeReq",
    "SetAIControlModeRsp",
    "SetDNSReq",
    "SetFrameDetectReq",
    "SetHdmiIdleTimeoutReq",
    "SetInputRegionReq",
    "SetMCPConfigReq",
    "SetMemoryLimitReq",
    "SetScreenReq",
    "SetSwapSizeReq",
    "SetTlsReq",
    "SetUpdateServerReq",
    "SetUserPasswordReq",
    "StopFrameDetectReq",
    "UpdateUserReq",
    "UploadAutostartReq",
    "UserInfo",
    "UserRole",
]


class ScreenSettingType(StrEnum):
    """Screen Setting types."""

    RESOLUTION = "resolution"  # removed in 2.5.4: video follows the HDMI input
    FPS = "fps"
    QUALITY = "quality"
    TYPE = "type"  # 0 = MJPEG, any other value = H.264
    GOP = "gop"  # 1-100; anything else falls back to 30


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


class SetTlsReq(BaseModel):
    enabled: bool


class SetFrameDetectReq(BaseModel):
    enabled: bool


class StopFrameDetectReq(BaseModel):
    duration: int


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


class GetAutostartRsp(BaseModel):
    files: list[str] = Field(default_factory=list)

    @field_validator("files", mode="before")
    @classmethod
    def _normalize_files(cls, value: Any) -> Any:
        return [] if value is None else value


class UploadAutostartReq(BaseModel):
    content: str


class GetUpdateServerRsp(BaseModel):
    enabled: bool
    url: str = Field(repr=False)


class SetUpdateServerReq(BaseModel):
    enabled: bool
    url: str = Field(repr=False)


class UserRole(StrEnum):
    """User account roles."""

    ADMIN = "admin"
    USER = "user"


class Permission(StrEnum):
    """Account permissions, from non-Pro 2.5.3. Administrators hold them all."""

    CONSOLE_VIEW = "console.view"
    CONSOLE_CONTROL = "console.control"
    POWER = "power"
    MEDIUM_ATTACH = "medium.attach"
    LIBRARY_READ = "library.read"
    LIBRARY_WRITE = "library.write"
    TERMINAL = "terminal"
    DEVICE = "device"
    NETWORK = "network"
    UPDATE = "update"
    MCP = "mcp"
    TAILSCALE = "tailscale"
    AI = "ai"


class PermissionInfo(BaseModel):
    id: str
    group: str
    risk: str  # "info", or "warn" for a grant that amounts to full device control
    implies: list[str] = Field(default_factory=list)

    @field_validator("implies", mode="before")
    @classmethod
    def _normalize_implies(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class GetPermissionsRsp(BaseModel):
    permissions: list[PermissionInfo]


class UserInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str
    role: str
    enabled: bool
    permissions: list[str] = Field(default_factory=list)  # stored grants, 2.5.3
    system_account: bool = Field(default=False, alias="systemAccount")

    @field_validator("permissions", mode="before")
    @classmethod
    def _normalize_permissions(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class GetUsersRsp(BaseModel):
    users: list[UserInfo]


class CreateUserReq(BaseModel):
    username: str
    password: str = Field(repr=False)
    role: str
    permissions: list[str] | None = None


class UpdateUserReq(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str | None = None
    enabled: bool | None = None
    permissions: list[str] | None = None
    rotate_mcp_key: bool | None = Field(default=None, alias="rotateMcpKey")


class SetUserPasswordReq(BaseModel):
    password: str = Field(repr=False)


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


class MediaType(StrEnum):
    """What a virtual medium is: an image file or the shared data partition."""

    IMAGE = "image"
    PARTITION = "partition"


class MediaItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    rel: str  # image path relative to the media directory; empty for the partition
    name: str
    size: int
    fs: str
    browsable: bool  # the device can list the files inside
    attached: bool
    modified_at: datetime = Field(alias="modifiedAt")


class MediaCapabilities(BaseModel):
    loop: bool


class GetMediaRsp(BaseModel):
    media: list[MediaItem] = Field(default_factory=list)
    capabilities: MediaCapabilities

    @field_validator("media", mode="before")
    @classmethod
    def _normalize_media(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class GetMediaStateRsp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    state: str  # none | image | partition | unsupported (virtual disk off)
    type: str
    rel: str
    hid_only: bool = Field(alias="hidOnly")  # the gadget has no mass storage
    open: list[str] = Field(default_factory=list)  # media the file manager holds
    cdrom: bool
    local_writable: bool | None = Field(default=None, alias="localWritable")  # 2.5.4

    @field_validator("open", mode="before")
    @classmethod
    def _normalize_open(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class GetMediaStatusRsp(BaseModel):
    status: str
    name: str
    percentage: str
    loaded: int
    total: int  # 0 when unknown
    message: str


class MediaFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    size: int
    is_dir: bool = Field(default=False, alias="isDir")
    modified_at: datetime | None = Field(default=None, alias="modifiedAt")


class GetMediaFilesRsp(BaseModel):
    path: str
    entries: list[MediaFile] = Field(default_factory=list)
    free: int
    total: int
    # A zero-length entry is a transfer in progress while this is true.
    writing: bool

    @field_validator("entries", mode="before")
    @classmethod
    def _normalize_entries(cls, value: Any) -> Any:
        return _normalize_string_list(value)


class MediaRefReq(BaseModel):
    type: MediaType
    rel: str = ""


class MountMediaReq(MediaRefReq):
    cdrom: bool = False
    force: bool = False


class EjectMediaReq(BaseModel):
    force: bool = False


class MediaPathReq(MediaRefReq):
    model_config = ConfigDict(populate_by_name=True)

    path: str
    exists_ok: bool | None = Field(default=None, alias="existsOk")
