"""Internal network operations for the NanoKVM session."""

from __future__ import annotations

from aiohttp import hdrs

from ..compatibility import require_application_version, require_hardware
from ..exceptions import NanoKVMApiError
from ..models.common import (
    ApiResponseCode,
    ConnectWifiReq,
    DeleteMacReq,
    GetMacRsp,
    GetWifiRsp,
    HWFamily,
    SetMacNameReq,
    WakeOnLANReq,
)
from ..models.non_pro import DNSMode, GetDNSRsp, SetDNSReq
from ..models.pro import GetStaticIPRsp, ScanWifiRsp, SetStaticIPReq
from .session import Controller


class NetworkController(Controller):
    """Implement network operations behind the public client facade."""

    async def get_wifi_status(self) -> GetWifiRsp:
        """Get WiFi status."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/network/wifi",
            response_model=GetWifiRsp,
        )

    @require_application_version(non_pro="2.3.1")
    async def connect_wifi(self, ssid: str, password: str) -> None:
        """Connect to a WiFi network."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/network/wifi/connect",
            data=ConnectWifiReq(ssid=ssid, password=password),
        )

    async def connect_wifi_no_auth(
        self,
        ssid: str,
        password: str = "",
        ap_password: str | None = None,
    ) -> None:
        """Connect to WiFi while the device is in AP-mode setup flow."""
        headers = {"X-AP-Key": ap_password} if ap_password is not None else {}
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/network/wifi",
            authenticate=False,
            headers=headers,
            data=ConnectWifiReq(ssid=ssid, password=password),
        )

    @require_application_version(non_pro="2.3.6", pro="1.2.14")
    async def verify_ap_login(self, ap_password: str) -> None:
        """Verify AP-mode setup credentials."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/network/wifi/verify",
            authenticate=False,
            headers={"X-AP-Key": ap_password},
        )

    @require_application_version(non_pro="2.3.1")
    async def disconnect_wifi(self) -> None:
        """Disconnect from the current WiFi network."""
        await self._session.api_request_json(hdrs.METH_POST, "/network/wifi/disconnect")

    async def send_wake_on_lan(self, mac: str) -> None:
        """Send a Wake-on-LAN packet."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/network/wol", data=WakeOnLANReq(mac=mac)
        )

    async def get_wol_macs(self) -> GetMacRsp:
        """Get saved Wake-on-LAN MAC entries."""
        try:
            return await self._session.api_request_json(
                hdrs.METH_GET,
                "/network/wol/mac",
                response_model=GetMacRsp,
            )
        except NanoKVMApiError as err:
            if (
                err.code == ApiResponseCode.INVALID_USERNAME_OR_PASSWORD.value
                and err.msg == "open file error"
            ):
                return GetMacRsp()
            raise

    async def delete_wol_mac(self, mac: str) -> None:
        """Delete a saved Wake-on-LAN MAC entry."""
        await self._session.api_request_json(
            hdrs.METH_DELETE,
            "/network/wol/mac",
            data=DeleteMacReq(mac=mac),
        )

    @require_application_version(non_pro="2.2.6")
    async def set_wol_mac_name(self, mac: str, name: str) -> None:
        """Set the display name for a saved Wake-on-LAN MAC entry."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/network/wol/mac/name",
            data=SetMacNameReq(mac=mac, name=name),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.4.1")
    async def get_dns(self) -> GetDNSRsp:
        """Get DNS configuration."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/network/dns",
            response_model=GetDNSRsp,
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.4.1")
    async def set_dns(
        self, mode: DNSMode | str, servers: list[str] | None = None
    ) -> None:
        """Set DNS configuration."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/network/dns",
            data=SetDNSReq(mode=DNSMode(mode), servers=servers or []),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def get_static_ip(self) -> GetStaticIPRsp:
        """Get static IP configuration."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/network/static-ip",
            response_model=GetStaticIPRsp,
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.2.3")
    async def set_static_ip(self, enabled: bool, ip: str) -> None:
        """Set static IP configuration."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/network/static-ip",
            data=SetStaticIPReq(enabled=enabled, ip=ip),
        )

    @require_hardware(HWFamily.PRO)
    @require_application_version(pro="1.1.7")
    async def scan_wifi(self) -> ScanWifiRsp:
        """Scan for available WiFi networks."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/network/wifi/scan",
            response_model=ScanWifiRsp,
        )
