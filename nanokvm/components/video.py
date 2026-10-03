"""Internal video operations for the NanoKVM session."""

from __future__ import annotations

from os import PathLike

from aiohttp import hdrs

from ..compatibility import require_application_version, require_hardware
from ..models.common import HWFamily, HWVersion
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
from .session import SessionController


class VideoController:
    """Implement video operations behind the public client facade."""

    def __init__(self, session: SessionController) -> None:
        self._session = session

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def get_input_region(self) -> GetInputRegionRsp:
        """Get the configured non-Pro input region."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/input-region",
            response_model=GetInputRegionRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
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
        await self._session.api_request_json(
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

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.5.1")
    async def get_input_resolution(self) -> GetInputResolutionRsp:
        """Get the current non-Pro input frame resolution."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/input-resolution",
            response_model=GetInputResolutionRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    async def set_screen(self, setting: ScreenSettingType, value: int) -> None:
        """Set a non-Pro NanoKVM screen setting."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/screen",
            data=SetScreenReq(type=setting, value=value),
        )

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def get_hdmi_state(self) -> GetHdmiStateRsp:
        """Get the HDMI state (PCIe variant)."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi",
            response_model=GetHdmiStateRsp,
        )

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.1.5")
    async def reset_hdmi(self) -> None:
        """Reset the HDMI connection."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/hdmi/reset")

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def enable_hdmi(self) -> None:
        """Enable the HDMI connection."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/hdmi/enable")

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.2.8")
    async def disable_hdmi(self) -> None:
        """Disable the HDMI connection."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/hdmi/disable")

    @require_hardware(HWVersion.PCIE)
    @require_application_version(non_pro="2.5.0")
    async def set_hdmi_idle_timeout(self, minutes: int) -> None:
        """Set the HDMI capture idle timeout in minutes; zero disables it."""
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise ValueError("minutes must be an integer between 0 and 10080")
        if not 0 <= minutes <= 10080:
            raise ValueError("minutes must be between 0 and 10080")

        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/timeout",
            data=SetHdmiIdleTimeoutReq(minutes=minutes),
        )

    @require_hardware(HWFamily.PRO)
    async def get_hdmi_capture(self) -> GetHdmiCaptureRsp:
        """Get HDMI capture status."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi/capture",
            response_model=GetHdmiCaptureRsp,
        )

    @require_hardware(HWFamily.PRO)
    async def set_hdmi_capture(self, enabled: bool) -> None:
        """Set HDMI capture status."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/capture",
            data=SetHdmiCaptureReq(enabled=enabled),
        )

    @require_hardware(HWFamily.PRO)
    async def get_hdmi_passthrough(self) -> GetHdmiPassthroughRsp:
        """Get HDMI passthrough status."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/hdmi/passthrough",
            response_model=GetHdmiPassthroughRsp,
        )

    @require_hardware(HWFamily.PRO)
    async def set_hdmi_passthrough(self, enabled: bool) -> None:
        """Set HDMI passthrough status."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/hdmi/passthrough",
            data=SetHdmiPassthroughReq(enabled=enabled),
        )

    @require_hardware(HWFamily.PRO)
    async def get_edid(self) -> GetEdidRsp:
        """Get current EDID."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/edid",
            response_model=GetEdidRsp,
        )

    @require_hardware(HWFamily.PRO)
    async def switch_edid(self, edid: EdidValue) -> None:
        """Switch EDID."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/edid",
            data=SwitchEdidReq(edid=edid),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def get_custom_edid_list(self) -> GetCustomEdidListRsp:
        """Get custom EDID list."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/edid/custom",
            response_model=GetCustomEdidListRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def upload_edid(self, file_path: str | PathLike[str]) -> UploadEdidRsp:
        """Upload a custom EDID."""
        return await self._session.upload_file(
            "/vm/edid/upload",
            file_path,
            response_model=UploadEdidRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def delete_edid(self, edid: str) -> None:
        """Delete a custom EDID."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/edid/delete",
            data=DeleteEdidReq(edid=edid),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.6")
    async def set_rate_control_mode(self, mode: RateControlMode) -> None:
        """Set the stream rate control mode (CBR/VBR)."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/stream/rate-control",
            data=SetRateControlModeReq(mode=mode),
        )

    @require_hardware(HWFamily.PRO)
    async def set_stream_mode(self, mode: StreamMode | str) -> None:
        """Set the stream mode."""
        stream_mode = mode if isinstance(mode, StreamMode) else StreamMode(mode)
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/stream/mode",
            data=SetStreamModeReq(mode=stream_mode),
        )

    @require_hardware(HWFamily.PRO)
    async def set_stream_quality(self, quality: int) -> None:
        """Set the stream quality / bit-rate."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/stream/quality",
            data=SetStreamQualityReq(quality=quality),
        )

    @require_hardware(HWFamily.PRO)
    async def set_gop(self, gop: int) -> None:
        """Set the stream GOP (Group of Pictures)."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/stream/gop",
            data=SetGopReq(gop=gop),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.8")
    async def set_fps(self, fps: int) -> None:
        """Set the stream FPS."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/stream/fps",
            data=SetFpsReq(fps=fps),
        )
