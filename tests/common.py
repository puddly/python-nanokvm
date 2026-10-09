"""Helpers shared by the test modules."""

from nanokvm.client import NanoKVMClient
from nanokvm.models import HWVersion

BASE_URL = "http://localhost:8888/api/"


def mark_detected(
    client: NanoKVMClient,
    hw_version: HWVersion = HWVersion.PCIE,
    application_version: str | None = "9.9.9",
) -> None:
    """Preset the hardware and application version a client would detect."""
    client._session._hw_version = hw_version
    client._session._application_version = application_version


def info_payload(
    application: str = "2.5.1", image: str = "v1.4.0"
) -> dict[str, object]:
    """Build a successful ``/vm/info`` response envelope."""
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "ips": [],
            "mdns": "kvm.local",
            "image": image,
            "application": application,
            "deviceKey": "synthetic-device-key",
        },
    }
