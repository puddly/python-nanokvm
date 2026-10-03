"""Tests for non-Pro MCP management and coordinated AI control."""

from datetime import UTC, datetime
import logging

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import (
    NanoKVMApiError,
    NanoKVMClient,
    NanoKVMNotSupportedError,
    NanoKVMPermissionError,
)
from nanokvm.models import (
    AIControlMode,
    AIControlStatusRsp,
    GetMCPConfigRsp,
    HWVersion,
    SetAIControlModeRsp,
)

_BASE_URL = "http://localhost:8888/api/"
_API_KEY = "synthetic-mcp-key"


def _mcp_config_payload() -> dict[str, object]:
    return {
        "enabled": True,
        "apiKey": _API_KEY,
        "controlMode": "mcp",
        "transitioning": False,
    }


def test_mcp_config_model_parses_aliases_without_revealing_key_in_repr() -> None:
    """MCP config exposes aliases while keeping the credential out of repr."""
    config = GetMCPConfigRsp.model_validate(_mcp_config_payload())

    assert config.enabled is True
    assert config.api_key == _API_KEY
    assert config.control_mode is AIControlMode.MCP
    assert config.transitioning is False
    assert _API_KEY not in repr(config)
    assert _API_KEY not in str(config)


def test_ai_control_status_model_parses_firmware_snake_case() -> None:
    """Control status keeps optional transition diagnostics."""
    status = AIControlStatusRsp.model_validate(
        {
            "mode": "picoclaw",
            "transitioning": True,
            "last_error": "synthetic failure",
            "changed_at": "2026-09-29T20:00:00Z",
        }
    )

    assert status.mode is AIControlMode.PICOCLAW
    assert status.transitioning is True
    assert status.last_error == "synthetic failure"
    assert status.changed_at == datetime(2026, 9, 29, 20, tzinfo=UTC)


def test_set_control_mode_model_normalizes_plain_and_wrapped_responses() -> None:
    """Mode switches expose one stable model for both firmware response shapes."""
    plain = SetAIControlModeRsp.model_validate({"mode": "mcp", "transitioning": False})
    wrapped = SetAIControlModeRsp.model_validate(
        {
            "control": {
                "mode": "off",
                "transitioning": False,
                "changed_at": "2026-09-29T20:00:00Z",
            },
            "released": True,
            "closed_sessions": 2,
            "cleanup_warning": "synthetic warning",
            "runtime": {"status": "ready"},
        }
    )

    assert plain.mode is AIControlMode.MCP
    assert plain.released is None
    assert wrapped.mode is AIControlMode.OFF
    assert wrapped.released is True
    assert wrapped.closed_sessions == 2
    assert wrapped.cleanup_warning == "synthetic warning"
    assert wrapped.runtime == {"status": "ready"}


async def test_mcp_management_routes_and_payloads() -> None:
    """Config reads, toggles and key regeneration use the 2.5.0 routes."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.0"

        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}mcp/config",
                payload={"code": 0, "msg": "success", "data": _mcp_config_payload()},
            )
            mocked.post(
                f"{_BASE_URL}mcp/config",
                payload={"code": 0, "msg": "success", "data": _mcp_config_payload()},
            )
            mocked.post(
                f"{_BASE_URL}mcp/key/regenerate",
                payload={"code": 0, "msg": "success", "data": _mcp_config_payload()},
            )

            read = await client.get_mcp_config()
            changed = await client.set_mcp_enabled(True)
            regenerated = await client.regenerate_mcp_api_key()

        assert read.api_key == _API_KEY
        assert changed.control_mode is AIControlMode.MCP
        assert regenerated.api_key == _API_KEY
        config_post = mocked.requests[("POST", yarl.URL(f"{_BASE_URL}mcp/config"))][0]
        assert config_post.kwargs.get("json") == {"enabled": True}
        key_post = mocked.requests[
            ("POST", yarl.URL(f"{_BASE_URL}mcp/key/regenerate"))
        ][0]
        assert key_post.kwargs.get("json") is None


async def test_mcp_config_response_does_not_log_api_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Successful config parsing never writes the MCP credential to logs."""
    caplog.set_level(logging.DEBUG, logger="nanokvm.client")
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.0"

        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}mcp/config",
                payload={"code": 0, "msg": "success", "data": _mcp_config_payload()},
            )
            config = await client.get_mcp_config()

    assert config.api_key == _API_KEY
    assert _API_KEY not in caplog.text


async def test_ai_control_routes_parse_plain_and_wrapped_results() -> None:
    """Status and PUT mode handle both official success payload shapes."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.2"

        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}ai/control/status",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {"mode": "mcp", "transitioning": False},
                },
            )
            mocked.put(
                f"{_BASE_URL}ai/control/mode",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {"mode": "picoclaw", "transitioning": False},
                },
            )
            mocked.put(
                f"{_BASE_URL}ai/control/mode",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "control": {"mode": "off", "transitioning": False},
                        "runtime": {"status": "ready"},
                        "released": True,
                        "closed_sessions": 0,
                        "cleanup_warning": "",
                    },
                },
            )

            status = await client.get_ai_control_status()
            picoclaw = await client.set_ai_control_mode(AIControlMode.PICOCLAW)
            off = await client.set_ai_control_mode(AIControlMode.OFF)

        assert status.mode is AIControlMode.MCP
        assert picoclaw.mode is AIControlMode.PICOCLAW
        assert off.mode is AIControlMode.OFF
        assert off.released is True
        calls = mocked.requests[("PUT", yarl.URL(f"{_BASE_URL}ai/control/mode"))]
        assert calls[0].kwargs.get("json") == {"mode": "picoclaw"}
        assert calls[1].kwargs.get("json") == {"mode": "off"}


@pytest.mark.parametrize(
    "method_name",
    [
        "get_mcp_config",
        "set_mcp_enabled",
        "regenerate_mcp_api_key",
        "get_ai_control_status",
        "set_ai_control_mode",
    ],
)
@pytest.mark.parametrize(
    ("hardware", "version", "message"),
    [
        (HWVersion.PRO, "9.9.9", "hardware family: non-Pro"),
        (HWVersion.PCIE, "2.4.9", "application version >= 2.5.0"),
    ],
)
async def test_mcp_methods_reject_unsupported_devices_before_io(
    method_name: str,
    hardware: HWVersion,
    version: str,
    message: str,
) -> None:
    """MCP management is limited to non-Pro application 2.5.0 and later."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = hardware
        client._session._application_version = version
        args: tuple[object, ...] = ()
        if method_name == "set_mcp_enabled":
            args = (True,)
        elif method_name == "set_ai_control_mode":
            args = (AIControlMode.OFF,)

        with (
            aioresponses() as mocked,
            pytest.raises(NanoKVMNotSupportedError, match=message),
        ):
            await getattr(client, method_name)(*args)

        assert not mocked.requests


async def test_mcp_permission_error_does_not_expose_api_key() -> None:
    """A management 403 uses the shared permission error without credentials."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.1"

        with aioresponses() as mocked:
            mocked.get(f"{_BASE_URL}mcp/config", status=403, body=b'"forbidden"')
            with pytest.raises(NanoKVMPermissionError) as exc_info:
                await client.get_mcp_config()

        assert exc_info.value.status == 403
        assert _API_KEY not in str(exc_info.value)


async def test_control_error_message_alias_remains_api_error() -> None:
    """The control router's `message` envelope field remains an API error."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = HWVersion.PCIE
        client._session._application_version = "2.5.0"

        with aioresponses() as mocked:
            mocked.put(
                f"{_BASE_URL}ai/control/mode",
                payload={"code": -1, "message": "invalid control mode"},
            )
            with pytest.raises(NanoKVMApiError) as exc_info:
                await client.set_ai_control_mode(AIControlMode.MCP)

        assert exc_info.value.code == -1
        assert exc_info.value.msg == "invalid control mode"
