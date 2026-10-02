"""Tests for the non-Pro input-region API."""

from aioresponses import aioresponses
from pydantic import ValidationError
import pytest
import yarl

from nanokvm.client import NanoKVMApiError, NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import (
    GetInputRegionRsp,
    GetInputResolutionRsp,
    HWVersion,
    InputRegionMode,
    ManualRegion,
    OriginalResolution,
    SetInputRegionReq,
)

_BASE_URL = "http://localhost:8888/api/"


def test_input_region_models_parse_aliases_and_off_defaults() -> None:
    """An off response may contain only its mode; populated fields parse aliases."""
    off = GetInputRegionRsp.model_validate({"mode": "off"})

    assert off.mode is InputRegionMode.OFF
    assert off.frame_width == 0
    assert off.frame_height == 0
    assert off.left == 0
    assert off.top == 0
    assert off.width == 0
    assert off.height == 0
    assert off.resolutions == []
    assert off.selected_resolution == ""
    assert off.regions == []
    assert off.selected_region == ""

    populated = GetInputRegionRsp.model_validate(
        {
            "mode": "manual",
            "frameWidth": 1920,
            "frameHeight": 1080,
            "left": 10,
            "top": 20,
            "width": 800,
            "height": 600,
            "resolutions": [{"width": 1920, "height": 1080}],
            "selectedResolution": "1920x1080",
            "regions": [
                {
                    "frameWidth": 1920,
                    "frameHeight": 1080,
                    "left": 10,
                    "top": 20,
                    "width": 800,
                    "height": 600,
                }
            ],
            "selectedRegion": "800x600",
        }
    )

    assert populated.frame_width == 1920
    assert populated.selected_resolution == "1920x1080"
    assert populated.resolutions == [OriginalResolution(width=1920, height=1080)]
    assert populated.regions == [
        ManualRegion(
            frame_width=1920,
            frame_height=1080,
            left=10,
            top=20,
            width=800,
            height=600,
        )
    ]
    assert populated.selected_region == "800x600"


def test_input_region_request_requires_mode_and_preserves_empty_values() -> None:
    """Unset fields are omitted while explicit empty strings and lists survive."""
    request = SetInputRegionReq(
        mode=InputRegionMode.AUTO,
        frame_width=0,
        resolutions=[],
        selected_resolution="",
        regions=[],
        selected_region="",
    )

    assert request.model_dump(by_alias=True, exclude_none=True) == {
        "mode": "auto",
        "frameWidth": 0,
        "resolutions": [],
        "selectedResolution": "",
        "regions": [],
        "selectedRegion": "",
    }
    assert SetInputRegionReq(mode=InputRegionMode.OFF).model_dump(
        by_alias=True, exclude_none=True
    ) == {"mode": "off"}

    with pytest.raises(ValidationError):
        SetInputRegionReq.model_validate({})


def test_input_resolution_model_parses_response() -> None:
    """The resolution response exposes its frame dimensions."""
    assert GetInputResolutionRsp.model_validate(
        {"width": 1920, "height": 1080}
    ) == GetInputResolutionRsp(width=1920, height=1080)


async def test_get_input_region_uses_non_pro_endpoint_and_parses_response() -> None:
    """Input-region state is read from the non-Pro route."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.1"

        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}vm/input-region",
                payload={"code": 0, "msg": "success", "data": {"mode": "off"}},
            )

            result = await client.get_input_region()

        assert result == GetInputRegionRsp(mode=InputRegionMode.OFF)
        assert (
            len(mocked.requests[("GET", yarl.URL(f"{_BASE_URL}vm/input-region"))]) == 1
        )


async def test_set_input_region_posts_complete_and_partial_payloads() -> None:
    """Input-region updates use aliases and omit only values left as None."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.1"
        url = f"{_BASE_URL}vm/input-region"

        with aioresponses() as mocked:
            mocked.post(url, payload={"code": 0, "msg": "success", "data": None})
            mocked.post(url, payload={"code": 0, "msg": "success", "data": None})

            await client.set_input_region(
                InputRegionMode.MANUAL,
                frame_width=1920,
                frame_height=1080,
                left=10,
                top=20,
                width=800,
                height=600,
                resolutions=[OriginalResolution(width=1920, height=1080)],
                selected_resolution="1920x1080",
                regions=[
                    ManualRegion(
                        frame_width=1920,
                        frame_height=1080,
                        left=10,
                        top=20,
                        width=800,
                        height=600,
                    )
                ],
                selected_region="800x600",
            )
            await client.set_input_region(
                InputRegionMode.AUTO,
                resolutions=[],
                selected_resolution="",
                regions=[],
                selected_region="",
            )

        calls = mocked.requests[("POST", yarl.URL(url))]
        assert calls[0].kwargs.get("json") == {
            "mode": "manual",
            "frameWidth": 1920,
            "frameHeight": 1080,
            "left": 10,
            "top": 20,
            "width": 800,
            "height": 600,
            "resolutions": [{"width": 1920, "height": 1080}],
            "selectedResolution": "1920x1080",
            "regions": [
                {
                    "frameWidth": 1920,
                    "frameHeight": 1080,
                    "left": 10,
                    "top": 20,
                    "width": 800,
                    "height": 600,
                }
            ],
            "selectedRegion": "800x600",
        }
        assert calls[1].kwargs.get("json") == {
            "mode": "auto",
            "resolutions": [],
            "selectedResolution": "",
            "regions": [],
            "selectedRegion": "",
        }


async def test_get_input_resolution_uses_non_pro_endpoint() -> None:
    """The current display resolution is read from its dedicated route."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.ALPHA
        client._session._application_version = "2.5.1"

        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}vm/input-resolution",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {"width": 1920, "height": 1080},
                },
            )

            result = await client.get_input_resolution()

        assert result == GetInputResolutionRsp(width=1920, height=1080)
        assert (
            len(mocked.requests[("GET", yarl.URL(f"{_BASE_URL}vm/input-resolution"))])
            == 1
        )


@pytest.mark.parametrize(
    "method_name", ["get_input_region", "set_input_region", "get_input_resolution"]
)
async def test_input_region_methods_reject_pro_without_io(method_name: str) -> None:
    """Input-region endpoints are unavailable on Pro hardware."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PRO
        client._session._application_version = "9.9.9"

        with (
            aioresponses() as mocked,
            pytest.raises(
                NanoKVMNotSupportedError,
                match="hardware family: non-Pro",
            ),
        ):
            if method_name == "set_input_region":
                await getattr(client, method_name)(InputRegionMode.OFF)
            else:
                await getattr(client, method_name)()

        assert not mocked.requests


@pytest.mark.parametrize(
    "method_name", ["get_input_region", "set_input_region", "get_input_resolution"]
)
async def test_input_region_methods_reject_250_without_io(method_name: str) -> None:
    """Input-region endpoints are unavailable before non-Pro 2.5.1."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.0"

        with (
            aioresponses() as mocked,
            pytest.raises(
                NanoKVMNotSupportedError,
                match=rf"{method_name} requires non-Pro application version >= 2.5.1",
            ),
        ):
            if method_name == "set_input_region":
                await getattr(client, method_name)(InputRegionMode.OFF)
            else:
                await getattr(client, method_name)()

        assert not mocked.requests


async def test_set_input_region_preserves_api_error() -> None:
    """An API error envelope remains a NanoKVMApiError."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.1"

        with aioresponses() as mocked:
            mocked.post(
                f"{_BASE_URL}vm/input-region",
                payload={"code": -1, "msg": "invalid input region", "data": None},
            )

            with pytest.raises(NanoKVMApiError) as exc_info:
                await client.set_input_region(InputRegionMode.MANUAL)

        assert exc_info.value.code == -1
        assert exc_info.value.msg == "invalid input region"
