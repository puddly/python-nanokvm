"""Helpers shared by the test modules."""

from nanokvm.client import NanoKVMClient
from nanokvm.models import HWVersion


def mark_detected(
    client: NanoKVMClient,
    hw_version: HWVersion = HWVersion.PCIE,
    application_version: str | None = "9.9.9",
) -> None:
    """Preset the hardware and application version a client would detect."""
    client._session._hw_version = hw_version
    client._session._application_version = application_version
