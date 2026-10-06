"""Internal system operations for the NanoKVM session."""

from __future__ import annotations

from aiohttp import hdrs

from ..compatibility import require_application_version, require_hardware
from ..models.common import (
    GetGpioRsp,
    GetHardwareRsp,
    GetHostnameRsp,
    GetInfoRsp,
    GetMdnsStateRsp,
    GetOLEDRsp,
    GetSSHStateRsp,
    GetVirtualDeviceRsp,
    GetWebTitleRsp,
    GpioType,
    HWFamily,
    SetGpioReq,
    SetHostnameReq,
    SetOledReq,
    SetWebTitleReq,
    UpdateVirtualDeviceReq,
    VirtualDevice,
)
from ..models.non_pro import (
    GetMemoryLimitRsp,
    GetSwapSizeRsp,
    SetMemoryLimitReq,
    SetSwapSizeReq,
)
from ..models.pro import (
    DiskType,
    GetLcdTimeFormatRsp,
    GetLedStripRsp,
    GetLowPowerRsp,
    GetMenuBarConfigRsp,
    GetTimeStatusRsp,
    GetTimeZoneRsp,
    LcdTimeFormat,
    RefreshVirtualDeviceReq,
    SetLcdTimeFormatReq,
    SetLedStripReq,
    SetLowPowerReq,
    SetMenuBarConfigReq,
    SetTimeZoneReq,
)
from .session import Controller


class SystemController(Controller):
    """Implement system operations behind the public client facade."""

    async def get_info(self) -> GetInfoRsp:
        """Get general device information."""
        return await self._session.get_info()

    async def get_hardware(self) -> GetHardwareRsp:
        """Get hardware version information."""
        return await self._session.get_hardware()

    @require_application_version(non_pro="2.2.6")
    async def get_hostname(self) -> GetHostnameRsp:
        """Get the configured hostname."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/hostname",
            response_model=GetHostnameRsp,
        )

    @require_application_version(non_pro="2.2.6")
    async def set_hostname(self, hostname: str) -> None:
        """Set the device hostname (applies after reboot)."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/hostname",
            data=SetHostnameReq(hostname=hostname),
        )

    async def get_gpio(self) -> GetGpioRsp:
        """Get GPIO LED status."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/gpio",
            response_model=GetGpioRsp,
        )

    async def push_button(self, button: GpioType, duration_ms: int) -> None:
        """Simulate pushing a hardware button."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/gpio",
            data=SetGpioReq(type=button, duration=duration_ms),
        )

    @require_application_version(non_pro="2.1.6")
    async def get_ssh_state(self) -> GetSSHStateRsp:
        """Get SSH enabled state."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/ssh",
            response_model=GetSSHStateRsp,
        )

    @require_application_version(non_pro="2.1.6")
    async def enable_ssh(self) -> None:
        """Enable SSH server."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/ssh/enable")

    @require_application_version(non_pro="2.1.6")
    async def disable_ssh(self) -> None:
        """Disable SSH server."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/ssh/disable")

    @require_application_version(non_pro="2.2.2")
    async def get_mdns_state(self) -> GetMdnsStateRsp:
        """Get mDNS enabled state."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/mdns",
            response_model=GetMdnsStateRsp,
        )

    @require_application_version(non_pro="2.2.2")
    async def enable_mdns(self) -> None:
        """Enable mDNS."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/mdns/enable")

    @require_application_version(non_pro="2.2.2")
    async def disable_mdns(self) -> None:
        """Disable mDNS."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/mdns/disable")

    async def get_oled_info(self) -> GetOLEDRsp:
        """Get OLED information."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/oled",
            response_model=GetOLEDRsp,
        )

    async def set_oled_sleep(self, sleep_seconds: int) -> None:
        """Set the OLED sleep timeout."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/oled",
            data=SetOledReq(sleep=sleep_seconds),
        )

    async def get_virtual_device_status(self) -> GetVirtualDeviceRsp:
        """Get the status of virtual devices."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/device/virtual",
            response_model=GetVirtualDeviceRsp,
        )

    async def update_virtual_device(
        self,
        device: VirtualDevice,
        *,
        disk_type: DiskType | None = None,  # Pro only
    ) -> None:
        """Toggle the state of a virtual device."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/device/virtual",
            data=UpdateVirtualDeviceReq(
                device=device,
                type=disk_type.value if disk_type else None,
            ),
        )

    @require_application_version(non_pro="2.2.6")
    async def get_web_title(self) -> GetWebTitleRsp:
        """Get the web page title."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/web-title",
            response_model=GetWebTitleRsp,
        )

    @require_application_version(non_pro="2.2.6")
    async def set_web_title(self, title: str) -> None:
        """Set the web page title."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/web-title",
            data=SetWebTitleReq(title=title),
        )

    @require_application_version(non_pro="2.2.2")
    async def reboot_system(self) -> None:
        """Reboot the KVM device."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/system/reboot")

    @require_hardware(HWFamily.PRO)
    async def switch_to_pikvm(self) -> None:
        """Switch the system image to PiKVM."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/system/pikvm")

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.6")
    async def get_swap_size(self) -> int:
        """Get Swap size."""
        rsp = await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/swap",
            response_model=GetSwapSizeRsp,
        )
        return rsp.size

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.6")
    async def set_swap_size(self, size_mb: int) -> None:
        """Set the Swap size."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/vm/swap", data=SetSwapSizeReq(size=size_mb)
        )

    @require_hardware(HWFamily.NON_PRO)
    async def get_memory_limit(self) -> GetMemoryLimitRsp:
        """Get the configured Go memory limit."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/memory/limit",
            response_model=GetMemoryLimitRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    async def set_memory_limit(self, enabled: bool, limit_mb: int) -> None:
        """Set or disable the Go memory limit."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/memory/limit",
            data=SetMemoryLimitReq(enabled=enabled, limit=limit_mb),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def refresh_virtual_device(self, device: str) -> None:
        """Refresh a virtual device (e.g. emmc)."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/device/virtual/refresh",
            data=RefreshVirtualDeviceReq(device=device),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_lcd_time_format(self) -> GetLcdTimeFormatRsp:
        """Get the LCD time format."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/lcd/time/format",
            response_model=GetLcdTimeFormatRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def set_lcd_time_format(self, fmt: LcdTimeFormat | str) -> None:
        """Set the LCD time format (12h/24h)."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/lcd/time/format",
            data=SetLcdTimeFormatReq(format=LcdTimeFormat(fmt)),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def get_low_power(self) -> GetLowPowerRsp:
        """Get low power status."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/low-power",
            response_model=GetLowPowerRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.7")
    async def set_low_power(self, enable: bool) -> None:
        """Set low power mode."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/low-power",
            data=SetLowPowerReq(enable=enable),
        )

    @require_hardware(HWFamily.PRO)
    async def get_led_strip(self) -> GetLedStripRsp:
        """Get LED strip configuration."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/ledstrip/get",
            response_model=GetLedStripRsp,
        )

    @require_hardware(HWFamily.PRO)
    async def set_led_strip(
        self,
        *,
        on: bool | None = None,
        horizontal_count: int | None = None,
        vertical_count: int | None = None,
        brightness: int | None = None,
    ) -> None:
        """Set LED strip configuration."""
        if all(
            value is None
            for value in (on, horizontal_count, vertical_count, brightness)
        ):
            raise ValueError("At least one LED strip setting must be provided")

        if any(
            value is None
            for value in (on, horizontal_count, vertical_count, brightness)
        ):
            current = await self.get_led_strip()
            on = current.on if on is None else on
            horizontal_count = (
                current.horizontal_count
                if horizontal_count is None
                else horizontal_count
            )
            vertical_count = (
                current.vertical_count if vertical_count is None else vertical_count
            )
            brightness = current.brightness if brightness is None else brightness

        assert on is not None
        assert horizontal_count is not None
        assert vertical_count is not None
        assert brightness is not None

        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/ledstrip/set",
            data=SetLedStripReq.model_validate(
                {
                    "on": on,
                    "hor": horizontal_count,
                    "ver": vertical_count,
                    "brightness": brightness,
                }
            ),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_timezone(self) -> GetTimeZoneRsp:
        """Get the configured timezone."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/timezone",
            response_model=GetTimeZoneRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def set_timezone(self, timezone: str) -> None:
        """Set the timezone."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/timezone",
            data=SetTimeZoneReq(timezone=timezone),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def get_time_status(self) -> GetTimeStatusRsp:
        """Get time synchronization status."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/time/status",
            response_model=GetTimeStatusRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.6")
    async def sync_time(self) -> None:
        """Synchronize time."""
        await self._session.api_request_json(hdrs.METH_POST, "/vm/time/sync")

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.10")
    async def get_menubar_config(self) -> GetMenuBarConfigRsp:
        """Get menu bar configuration."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/menubar",
            response_model=GetMenuBarConfigRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.10")
    async def set_menubar_config(self, disabled_items: list[str]) -> None:
        """Set menu bar configuration."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/menubar",
            data=SetMenuBarConfigReq.model_validate({"disabledItems": disabled_items}),
        )
