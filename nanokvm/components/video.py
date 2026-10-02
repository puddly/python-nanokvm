"""Internal video operations for the NanoKVM client."""

from __future__ import annotations

from os import PathLike
from typing import TYPE_CHECKING

from aiohttp import hdrs

from ..models.non_pro import (
    GetHdmiStateRsp,
    GetInputRegionRsp,
    GetInputResolutionRsp,
    InputRegionMode,
    ManualRegion,
    OriginalResolution,
    ScreenSettingType,
    SetHdmiIdleTimeoutReq,
    SetInputRegionReq,
    SetScreenReq,
)
from ..models.pro import (
    DeleteEdidReq,
    EdidValue,
    GetCustomEdidListRsp,
    GetEdidRsp,
    GetHdmiCaptureRsp,
    GetHdmiPassthroughRsp,
    RateControlMode,
    SetFpsReq,
    SetGopReq,
    SetHdmiCaptureReq,
    SetHdmiPassthroughReq,
    SetRateControlModeReq,
    SetStreamModeReq,
    SetStreamQualityReq,
    StreamMode,
    SwitchEdidReq,
    UploadEdidRsp,
)

if TYPE_CHECKING:
    from ..client import NanoKVMClient


class VideoController:
    """Implement video operations behind the public client facade."""

    def __init__(self, client: NanoKVMClient) -> None:
        self._client = client

    async def get_input_region(self) -> GetInputRegionRsp:
        """Get the configured non-Pro input region."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/vm/input-region",
            response_model=GetInputRegionRsp,
        )

    async def set_input_region(
        self,
        mode: InputRegionMode,
        *,
        frame_width: int | None = None,
        frame_height: int | None = None,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        resolutions: list[OriginalResolution] | None = None,
        selected_resolution: str | None = None,
        regions: list[ManualRegion] | None = None,
        selected_region: str | None = None,
    ) -> None:
        """Set the non-Pro input region configuration."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/vm/input-region",
            data=SetInputRegionReq(
                mode=mode,
                frame_width=frame_width,
                frame_height=frame_height,
                left=left,
                top=top,
                width=width,
                height=height,
                resolutions=resolutions,
                selected_resolution=selected_resolution,
                regions=regions,
                selected_region=selected_region,
            ),
        )

    async def get_input_resolution(self) -> GetInputResolutionRsp:
        """Get the current non-Pro input frame resolution."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/vm/input-resolution",
            response_model=GetInputResolutionRsp,
        )

    async def set_screen(self, setting: ScreenSettingType, value: int) -> None:
        """Set a non-Pro NanoKVM screen setting."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/vm/screen",
            data=SetScreenReq(type=setting, value=value),
        )

    async def get_hdmi_state(self) -> GetHdmiStateRsp:
        """Get the HDMI state (PCIe variant)."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi",
            response_model=GetHdmiStateRsp,
        )

    async def reset_hdmi(self) -> None:
        """Reset the HDMI connection."""
        await self._client._api_request_json(hdrs.METH_POST, "/vm/hdmi/reset")

    async def enable_hdmi(self) -> None:
        """Enable the HDMI connection."""
        await self._client._api_request_json(hdrs.METH_POST, "/vm/hdmi/enable")

    async def disable_hdmi(self) -> None:
        """Disable the HDMI connection."""
        await self._client._api_request_json(hdrs.METH_POST, "/vm/hdmi/disable")

    async def set_hdmi_idle_timeout(self, minutes: int) -> None:
        """Set the HDMI capture idle timeout in minutes; zero disables it."""
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise ValueError("minutes must be an integer between 0 and 10080")
        if not 0 <= minutes <= 10080:
            raise ValueError("minutes must be between 0 and 10080")

        await self._client._api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/timeout",
            data=SetHdmiIdleTimeoutReq(minutes=minutes),
        )

    async def get_hdmi_capture(self) -> GetHdmiCaptureRsp:
        """Get HDMI capture status."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi/capture",
            response_model=GetHdmiCaptureRsp,
        )

    async def set_hdmi_capture(self, enabled: bool) -> None:
        """Set HDMI capture status."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/capture",
            data=SetHdmiCaptureReq(enabled=enabled),
        )

    async def get_hdmi_passthrough(self) -> GetHdmiPassthroughRsp:
        """Get HDMI passthrough status."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi/passthrough",
            response_model=GetHdmiPassthroughRsp,
        )

    async def set_hdmi_passthrough(self, enabled: bool) -> None:
        """Set HDMI passthrough status."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/passthrough",
            data=SetHdmiPassthroughReq(enabled=enabled),
        )

    async def get_edid(self) -> GetEdidRsp:
        """Get current EDID."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/vm/edid",
            response_model=GetEdidRsp,
        )

    async def switch_edid(self, edid: EdidValue) -> None:
        """Switch EDID."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/vm/edid",
            data=SwitchEdidReq(edid=edid),
        )

    async def get_custom_edid_list(self) -> GetCustomEdidListRsp:
        """Get custom EDID list."""
        return await self._client._api_request_json(
            hdrs.METH_GET,
            "/vm/edid/custom",
            response_model=GetCustomEdidListRsp,
        )

    async def upload_edid(self, file_path: str | PathLike[str]) -> UploadEdidRsp:
        """Upload a custom EDID."""
        return await self._client._upload_file(
            "/vm/edid/upload",
            file_path,
            response_model=UploadEdidRsp,
        )

    async def delete_edid(self, edid: str) -> None:
        """Delete a custom EDID."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/vm/edid/delete",
            data=DeleteEdidReq(edid=edid),
        )

    async def set_rate_control_mode(self, mode: RateControlMode) -> None:
        """Set the stream rate control mode (CBR/VBR)."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/stream/rate-control",
            data=SetRateControlModeReq(mode=mode),
        )

    async def set_stream_mode(self, mode: StreamMode | str) -> None:
        """Set the stream mode."""
        stream_mode = mode if isinstance(mode, StreamMode) else StreamMode(mode)
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/stream/mode",
            data=SetStreamModeReq(mode=stream_mode),
        )

    async def set_stream_quality(self, quality: int) -> None:
        """Set the stream quality / bit-rate."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/stream/quality",
            data=SetStreamQualityReq(quality=quality),
        )

    async def set_gop(self, gop: int) -> None:
        """Set the stream GOP (Group of Pictures)."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/stream/gop",
            data=SetGopReq(gop=gop),
        )

    async def set_fps(self, fps: int) -> None:
        """Set the stream FPS."""
        await self._client._api_request_json(
            hdrs.METH_POST,
            "/stream/fps",
            data=SetFpsReq(fps=fps),
        )
