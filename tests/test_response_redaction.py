"""Malformed response values must not escape through chained tracebacks."""

import logging
import traceback

import aiohttp
from aioresponses import aioresponses
import pytest

from nanokvm.client import (
    NanoKVMApiError,
    NanoKVMAuthenticationFailure,
    NanoKVMClient,
    NanoKVMInvalidResponseError,
)

_URL = "http://kvm.local/api/"
_SECRET = "synthetic-secret-value"


@pytest.mark.parametrize("source", ["login-token", "envelope", "account", "form"])
async def test_invalid_response_traceback_and_exception_log_hide_payload(
    source: str, caplog: pytest.LogCaptureFixture
) -> None:
    """logger.exception renders causes as well as the top-level error message."""
    caplog.set_level(logging.DEBUG)
    async with NanoKVMClient(_URL, token="synthetic-session") as client:
        with aioresponses() as mocked:
            if source == "login-token":
                mocked.post(
                    f"{_URL}auth/login",
                    payload={"code": 0, "msg": "ok", "data": {"token": [_SECRET]}},
                )
            elif source == "envelope":
                mocked.get(
                    f"{_URL}auth/account",
                    payload={"code": [_SECRET], "msg": "ok", "data": None},
                )
            elif source == "form":
                mocked.post(f"{_URL}upload", payload={"code": [_SECRET]})
            else:
                mocked.get(
                    f"{_URL}auth/account",
                    payload={"code": 0, "msg": "ok", "data": {"username": [_SECRET]}},
                )
            try:
                if source == "login-token":
                    await client.authenticate("synthetic-user", "synthetic-password")
                elif source == "form":
                    await client._api_request_form(
                        "POST", "/upload", data=aiohttp.FormData()
                    )
                else:
                    await client.get_account()
            except NanoKVMInvalidResponseError as error:
                rendered = "".join(traceback.format_exception(error))
                logging.getLogger(__name__).exception("Device response rejected")
            else:
                pytest.fail("Expected a response validation error")
            assert _SECRET not in rendered
            assert _SECRET not in caplog.text


@pytest.mark.parametrize("code", [0, -1, -2])
async def test_server_messages_are_not_rendered_in_logs_or_errors(
    code: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Firmware text is untrusted and can echo a credential or token."""
    caplog.set_level(logging.DEBUG)
    async with NanoKVMClient(_URL, use_password_obfuscation=True) as client:
        with aioresponses() as mocked:
            mocked.post(
                f"{_URL}auth/login",
                payload={
                    "code": code,
                    "msg": _SECRET,
                    "data": {"token": "synthetic-token"},
                },
            )
            mocked.get(
                f"{_URL}vm/hardware",
                payload={"code": 0, "msg": "ok", "data": {"version": "PCIE"}},
            )
            try:
                await client.authenticate("synthetic-user", "synthetic-password")
            except (NanoKVMApiError, NanoKVMAuthenticationFailure) as error:
                assert code != 0
                assert _SECRET not in "".join(traceback.format_exception(error))
                if isinstance(error, NanoKVMApiError):
                    assert error.code == code
                    assert error.msg == _SECRET
            assert _SECRET not in caplog.text
