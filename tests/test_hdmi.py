"""Tests for HDMI API hardware and firmware requirements."""

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import GetHdmiStateRsp, HWVersion

_BASE_URL = "http://localhost:8888/api/"


@pytest.mark.parametrize("hardware", [HWVersion.ALPHA, HWVersion.BETA])
@pytest.mark.parametrize(
    "method_name",
    ["get_hdmi_state", "reset_hdmi", "enable_hdmi", "disable_hdmi"],
)
async def test_hdmi_methods_reject_non_pcie_hardware(
    hardware: HWVersion,
    method_name: str,
) -> None:
    """PCIe-only HDMI routes fail locally on other non-Pro hardware."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = hardware
        client._application_version = "9.9.9"

        with aioresponses() as mocked:
            with pytest.raises(
                NanoKVMNotSupportedError,
                match=rf"{method_name} requires hardware: PCIE",
            ):
                await getattr(client, method_name)()

            assert not mocked.requests


@pytest.mark.parametrize(
    ("method_name", "application_version", "minimum_version"),
    [
        ("get_hdmi_state", "2.2.7", "2.2.8"),
        ("reset_hdmi", "2.1.4", "2.1.5"),
        ("enable_hdmi", "2.2.7", "2.2.8"),
        ("disable_hdmi", "2.2.7", "2.2.8"),
    ],
)
async def test_hdmi_methods_reject_older_firmware(
    method_name: str,
    application_version: str,
    minimum_version: str,
) -> None:
    """HDMI routes fail locally before their first supported firmware."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = application_version

        with aioresponses() as mocked:
            error_pattern = (
                rf"{method_name} requires non-Pro application version "
                rf">= {minimum_version}"
            )
            with pytest.raises(
                NanoKVMNotSupportedError,
                match=error_pattern,
            ):
                await getattr(client, method_name)()

            assert not mocked.requests


@pytest.mark.parametrize(
    ("method_name", "application_version", "path"),
    [
        ("get_hdmi_state", "2.2.8", "vm/hdmi"),
        ("reset_hdmi", "2.1.5", "vm/hdmi/reset"),
        ("enable_hdmi", "2.2.8", "vm/hdmi/enable"),
        ("disable_hdmi", "2.2.8", "vm/hdmi/disable"),
    ],
)
async def test_hdmi_methods_accept_first_supported_firmware(
    method_name: str,
    application_version: str,
    path: str,
) -> None:
    """Each HDMI route is available at its first supported firmware."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = application_version

        payload = {
            "code": 0,
            "msg": "success",
            "data": {"enabled": True} if method_name == "get_hdmi_state" else None,
        }
        with aioresponses() as mocked:
            url = f"{_BASE_URL}{path}"
            if method_name == "get_hdmi_state":
                mocked.get(url, payload=payload)
            else:
                mocked.post(url, payload=payload)

            result = await getattr(client, method_name)()

            if method_name == "get_hdmi_state":
                assert result == GetHdmiStateRsp(enabled=True)
            else:
                assert result is None
            assert len(mocked.requests) == 1


def test_hdmi_state_keeps_old_responses_and_parses_new_metadata() -> None:
    """Optional HDMI metadata remains compatible with pre-2.5.0 responses."""
    legacy = GetHdmiStateRsp.model_validate({"enabled": True})
    version_250 = GetHdmiStateRsp.model_validate({"enabled": True, "idleTimeout": 15})
    current = GetHdmiStateRsp.model_validate(
        {"enabled": True, "signal": False, "idleTimeout": 30}
    )

    assert legacy == GetHdmiStateRsp(enabled=True)
    assert legacy.signal is None
    assert legacy.idle_timeout is None
    assert version_250.signal is None
    assert version_250.idle_timeout == 15
    assert current.signal is False
    assert current.idle_timeout == 30


@pytest.mark.parametrize("minutes", [0, 10080])
async def test_set_hdmi_idle_timeout_posts_boundary_values(minutes: int) -> None:
    """The PCIe timeout endpoint accepts the complete firmware range."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = "2.5.0"
        url = f"{_BASE_URL}vm/hdmi/timeout"

        with aioresponses() as mocked:
            mocked.post(
                url,
                payload={"code": 0, "msg": "success", "data": None},
            )
            await client.set_hdmi_idle_timeout(minutes)

        call = mocked.requests[("POST", yarl.URL(url))][0]
        assert call.kwargs.get("json") == {"minutes": minutes}


@pytest.mark.parametrize("minutes", [-1, 10081, True, 1.5])
async def test_set_hdmi_idle_timeout_rejects_invalid_values_before_io(
    minutes: object,
) -> None:
    """Invalid minute values fail locally without reaching the device."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = "2.5.0"

        with aioresponses() as mocked, pytest.raises(ValueError, match="minutes"):
            await client.set_hdmi_idle_timeout(minutes)  # type: ignore[arg-type]

        assert not mocked.requests


@pytest.mark.parametrize("hardware", [HWVersion.ALPHA, HWVersion.BETA, HWVersion.PRO])
async def test_set_hdmi_idle_timeout_rejects_non_pcie_before_io(
    hardware: HWVersion,
) -> None:
    """The timeout setting exists only on PCIe non-Pro hardware."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = hardware
        client._application_version = "9.9.9"

        with (
            aioresponses() as mocked,
            pytest.raises(NanoKVMNotSupportedError, match="hardware: PCIE"),
        ):
            await client.set_hdmi_idle_timeout(10)

        assert not mocked.requests


async def test_set_hdmi_idle_timeout_rejects_firmware_249_before_io() -> None:
    """The timeout setting starts with non-Pro application 2.5.0."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._hw_version = HWVersion.PCIE
        client._application_version = "2.4.9"

        with (
            aioresponses() as mocked,
            pytest.raises(
                NanoKVMNotSupportedError,
                match=(
                    "set_hdmi_idle_timeout requires non-Pro application version "
                    ">= 2.5.0"
                ),
            ),
        ):
            await client.set_hdmi_idle_timeout(10)

        assert not mocked.requests
