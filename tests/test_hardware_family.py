"""Tests for NanoKVM hardware family classification."""

from types import SimpleNamespace
from typing import cast

from aioresponses import aioresponses
import pytest

from nanokvm.client import (
    NanoKVMClient,
    NanoKVMError,
    NanoKVMNotSupportedError,
    require_hardware,
)
from nanokvm.models import HWFamily, HWVersion


@pytest.mark.parametrize(
    ("version", "family"),
    [
        (HWVersion.ALPHA, HWFamily.NON_PRO),
        (HWVersion.BETA, HWFamily.NON_PRO),
        (HWVersion.PCIE, HWFamily.NON_PRO),
        (HWVersion.PRO, HWFamily.PRO),
        (HWVersion.UNKNOWN, None),
    ],
)
def test_hardware_versions_map_to_families(
    version: HWVersion, family: HWFamily | None
) -> None:
    """Exact hardware versions expose their family without changing identity."""
    assert version.family is family


async def test_hardware_decorator_accepts_each_non_pro_version() -> None:
    """A non-Pro family requirement accepts Alpha, Beta, and PCIe."""

    @require_hardware(HWFamily.NON_PRO)
    async def operation(client: NanoKVMClient) -> str:
        return "ok"

    client = NanoKVMClient("http://kvm.local/api/")
    for version in (HWVersion.ALPHA, HWVersion.BETA, HWVersion.PCIE):
        client._session._hw_version = version
        assert await operation(client) == "ok"


async def test_hardware_decorator_preserves_exact_version_requirements() -> None:
    """Existing exact hardware requirements keep their original behavior."""

    @require_hardware(HWVersion.PRO)
    async def operation(client: NanoKVMClient) -> str:
        return "ok"

    client = NanoKVMClient("http://kvm.local/api/")
    client._session._hw_version = HWVersion.PRO
    assert await operation(client) == "ok"
    client._session._hw_version = HWVersion.PCIE
    with pytest.raises(NanoKVMNotSupportedError, match="hardware: Pro"):
        await operation(client)


async def test_pro_family_requirement_accepts_future_pro_hardware() -> None:
    """Pro-only operations accept future hardware versions in the Pro family."""
    async with NanoKVMClient("http://kvm.local/api/", token="test-token") as client:
        client._session._hw_version = cast(
            HWVersion, SimpleNamespace(family=HWFamily.PRO, value="Pro Desk PoE")
        )
        with aioresponses() as mocked:
            mocked.get(
                "http://kvm.local/api/vm/hdmi/capture",
                payload={"code": 0, "msg": "success", "data": {"enabled": True}},
            )

            result = await client.get_hdmi_capture()

            assert result.enabled is True
            assert len(mocked.requests) == 1


async def test_hardware_decorator_family_requirement_requires_detection() -> None:
    """A family requirement requires hardware detection first."""

    @require_hardware(HWFamily.NON_PRO)
    async def operation(client: NanoKVMClient) -> str:
        return "ok"

    client = NanoKVMClient("http://kvm.local/api/")
    with pytest.raises(NanoKVMError, match="requires hardware detection"):
        await operation(client)


@pytest.mark.parametrize("version", [HWVersion.PRO, HWVersion.UNKNOWN])
async def test_hardware_decorator_family_requirement_rejects_other_versions(
    version: HWVersion,
) -> None:
    """Pro and unknown hardware cannot use a non-Pro-only operation."""

    @require_hardware(HWFamily.NON_PRO)
    async def operation(client: NanoKVMClient) -> str:
        return "ok"

    client = NanoKVMClient("http://kvm.local/api/")
    client._session._hw_version = version
    with pytest.raises(NanoKVMNotSupportedError, match="hardware: non-Pro"):
        await operation(client)
