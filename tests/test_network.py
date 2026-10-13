"""Tests for Wake-on-LAN, Wi-Fi and DNS operations."""

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import DNSMode, HWVersion

from .common import BASE_URL, info_payload, mark_detected


async def test_get_wol_macs_returns_empty_list_for_missing_file(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test WOL getter normalizes the empty-file device state."""
    mocked.get(
        f"{BASE_URL}network/wol/mac",
        payload={"code": -2, "msg": "open file error", "data": None},
    )

    response = await client.get_wol_macs()

    assert response.macs == []


async def test_connect_wifi_no_auth_sends_ap_header(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test AP-mode Wi-Fi connection sends setup credentials without auth cookie."""
    mocked.post(
        f"{BASE_URL}network/wifi",
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.connect_wifi_no_auth(
        "NanoKVM-Setup",
        ap_password="setup-secret",
    )

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}network/wifi"))]
    assert calls[0].kwargs.get("json") == {
        "ssid": "NanoKVM-Setup",
        "password": "",
    }
    assert calls[0].kwargs.get("cookies") == {}
    assert calls[0].kwargs.get("headers", {})["X-AP-Key"] == "setup-secret"


async def test_get_dns_parses_configuration(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test DNS configuration response parsing."""
    mark_detected(client, application_version="2.4.1")

    mocked.get(
        f"{BASE_URL}network/dns",
        payload={
            "code": 0,
            "msg": "success",
            "data": {
                "mode": "manual",
                "servers": ["1.1.1.1", "2606:4700:4700::1111"],
                "effective": ["1.1.1.1"],
                "dhcp": ["192.168.1.1"],
                "info": {
                    "interface": "eth0",
                    "type": "Wired",
                    "address": "192.168.1.20/24",
                    "subnetMask": "255.255.255.0",
                    "gateway": "192.168.1.1",
                    "searchDomains": ["lan"],
                },
            },
        },
    )

    response = await client.get_dns()

    assert response.mode is DNSMode.MANUAL
    assert response.servers == ["1.1.1.1", "2606:4700:4700::1111"]
    assert response.effective == ["1.1.1.1"]
    assert response.dhcp == ["192.168.1.1"]
    assert response.info.interface == "eth0"
    assert response.info.type == "Wired"
    assert response.info.address == "192.168.1.20/24"
    assert response.info.subnet_mask == "255.255.255.0"
    assert response.info.gateway == "192.168.1.1"
    assert response.info.search_domains == ["lan"]


async def test_set_dns_manual_sends_servers(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test manual DNS mode sends the configured servers."""
    mark_detected(client, application_version="2.4.1")

    mocked.post(
        f"{BASE_URL}network/dns",
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.set_dns(
        DNSMode.MANUAL,
        ["1.1.1.1", "2606:4700:4700::1111"],
    )

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}network/dns"))]
    assert calls[0].kwargs.get("json") == {
        "mode": "manual",
        "servers": ["1.1.1.1", "2606:4700:4700::1111"],
    }


async def test_set_dns_dhcp_string_sends_empty_servers(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test DHCP DNS mode accepts string mode and sends empty servers."""
    mark_detected(client, application_version="2.4.1")

    mocked.post(
        f"{BASE_URL}network/dns",
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.set_dns("dhcp")

    calls = mocked.requests[("POST", yarl.URL(f"{BASE_URL}network/dns"))]
    assert calls[0].kwargs.get("json") == {
        "mode": "dhcp",
        "servers": [],
    }


async def test_get_dns_pro_is_not_supported(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test DNS management is limited to non-Pro hardware."""
    client._session._hw_version = HWVersion.PRO

    with pytest.raises(NanoKVMNotSupportedError) as exc_info:
        await client.get_dns()

    assert "get_dns requires hardware: non-Pro" in str(exc_info.value)
    assert not mocked.requests


async def test_get_dns_old_application_version_is_not_supported(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test old non-Pro application versions reject DNS before endpoint call."""
    client._session._hw_version = HWVersion.PCIE

    mocked.get(
        f"{BASE_URL}vm/info",
        payload=info_payload(application="2.4.0"),
    )

    with pytest.raises(NanoKVMNotSupportedError) as exc_info:
        await client.get_dns()

    assert "get_dns requires non-Pro application version >= 2.4.1" in str(
        exc_info.value
    )
    dns_url = yarl.URL(f"{BASE_URL}network/dns")
    assert ("GET", dns_url) not in mocked.requests


async def test_get_dns_exact_application_version_is_supported(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Test DNS is allowed at the first upstream version that introduced it."""
    client._session._hw_version = HWVersion.PCIE

    mocked.get(
        f"{BASE_URL}vm/info",
        payload=info_payload(application="2.4.1"),
    )
    mocked.get(
        f"{BASE_URL}network/dns",
        payload={
            "code": 0,
            "msg": "success",
            "data": {
                "mode": "dhcp",
                "servers": [],
                "effective": [],
                "dhcp": [],
                "info": {},
            },
        },
    )

    response = await client.get_dns()

    assert response.mode is DNSMode.DHCP
