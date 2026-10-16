"""Tests for application version checks and updates."""

from collections.abc import AsyncIterator
import hashlib
import logging
from pathlib import Path
from typing import Any

import aiohttp
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import (
    NanoKVMApiError,
    NanoKVMAuthenticationFailure,
    NanoKVMClient,
    NanoKVMError,
    NanoKVMNotAuthenticatedError,
    NanoKVMNotSupportedError,
)
from nanokvm.models import (
    ApiResponseCode,
    GetUpdateServerRsp,
    HWVersion,
    SetUpdateServerReq,
)

from .common import BASE_URL, info_payload, mark_detected

_LOGIN_URL = f"{BASE_URL}auth/login"
_VERSION_URL = f"{BASE_URL}application/version"
_UPDATE_URL = f"{BASE_URL}application/update"
_INFO_URL = f"{BASE_URL}vm/info"


def _ok(data: object = None) -> dict[str, object]:
    return {"code": 0, "msg": "success", "data": data}


def _versions(current: str, latest: str) -> dict[str, object]:
    return _ok({"current": current, "latest": latest})


def _calls(mocked: aioresponses, method: str, url: str) -> list[Any]:
    calls: list[Any] = mocked.requests.get((method, yarl.URL(url)), [])
    return calls


@pytest.fixture
async def kept_client(mocked: aioresponses) -> AsyncIterator[NanoKVMClient]:
    """A logged-in client on 2.4.3 that kept its credentials."""
    async with NanoKVMClient(
        BASE_URL, keep_credentials=True, use_password_obfuscation=False
    ) as client:
        mocked.post(_LOGIN_URL, payload=_ok({"token": "first-token"}))
        mocked.get(f"{BASE_URL}vm/hardware", payload=_ok({"version": "PCIE"}))
        await client.authenticate("synthetic-user", "synthetic-password")
        mark_detected(client, HWVersion.PCIE, "2.4.3")
        yield client


async def test_update_application_offline_uploads_package_and_checksum(
    tmp_path: Path,
    client: NanoKVMClient,
    mocked: aioresponses,
) -> None:
    """Offline updates use the firmware multipart field and checksum header."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    url = f"{BASE_URL}application/update/offline"

    mark_detected(client, application_version="2.5.1")

    mocked.post(
        url,
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.update_application_offline(package, sha256=checksum)

    call = mocked.requests[("POST", yarl.URL(url))][0]
    assert isinstance(call.kwargs.get("data"), aiohttp.FormData)
    assert call.kwargs["headers"]["X-SHA256-Checksum"] == checksum
    assert call.kwargs["timeout"].total == 15 * 60


async def test_update_application_offline_verifies_checksum_on_legacy_firmware(
    tmp_path: Path,
    client: NanoKVMClient,
    mocked: aioresponses,
) -> None:
    """Legacy firmware gets local checksum protection without an ignored header."""
    package = tmp_path / "nanokvm_2.5.0.tar.gz"
    package.write_bytes(b"synthetic update package")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    url = f"{BASE_URL}application/update/offline"

    mark_detected(client, application_version="2.5.0")

    mocked.post(
        url,
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.update_application_offline(package, sha256=checksum)

    call = mocked.requests[("POST", yarl.URL(url))][0]
    assert "X-SHA256-Checksum" not in call.kwargs["headers"]


async def test_update_application_offline_rejects_checksum_mismatch_before_io(
    tmp_path: Path,
    client: NanoKVMClient,
) -> None:
    """A mismatched package checksum fails before every supported upload contract."""
    package = tmp_path / "nanokvm_2.5.0.tar.gz"
    package.write_bytes(b"synthetic update package")

    mark_detected(client, application_version="2.3.1")

    with (
        aioresponses() as mocked,
        pytest.raises(ValueError, match="does not match"),
    ):
        await client.update_application_offline(package, sha256="a" * 64)

    assert not mocked.requests


async def test_update_application_offline_omits_empty_checksum(
    tmp_path: Path, client: NanoKVMClient, mocked: aioresponses
) -> None:
    """An omitted checksum does not send an empty checksum header."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")
    url = f"{BASE_URL}application/update/offline"

    mark_detected(client, application_version="2.3.1")

    mocked.post(
        url,
        payload={"code": 0, "msg": "success", "data": None},
    )

    await client.update_application_offline(package, sha256="  ")

    call = mocked.requests[("POST", yarl.URL(url))][0]
    assert "X-SHA256-Checksum" not in call.kwargs["headers"]


@pytest.mark.parametrize("sha256", ["a" * 63, "g" * 64])
async def test_update_application_offline_rejects_invalid_checksum_before_io(
    tmp_path: Path,
    sha256: str,
    client: NanoKVMClient,
) -> None:
    """Malformed checksums fail before a package is uploaded."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")

    mark_detected(client, application_version="2.3.1")

    with aioresponses() as mocked, pytest.raises(ValueError, match="hexadecimal"):
        await client.update_application_offline(package, sha256=sha256)

    assert not mocked.requests


async def test_update_application_offline_rejects_invalid_filename_before_io(
    tmp_path: Path,
    client: NanoKVMClient,
) -> None:
    """Only official NanoKVM application package names are accepted."""
    package = tmp_path / "update.tar.gz"
    package.write_bytes(b"synthetic update package")

    mark_detected(client, application_version="2.3.1")

    with aioresponses() as mocked, pytest.raises(ValueError, match="filename"):
        await client.update_application_offline(package)

    assert not mocked.requests


async def test_update_application_offline_accepts_restart_502(
    tmp_path: Path, client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A proxy 502 after the upload matches the official UI success handling."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")

    mark_detected(client, application_version="2.5.1")

    mocked.post(f"{BASE_URL}application/update/offline", status=502)
    await client.update_application_offline(package)


async def test_update_application_offline_preserves_legacy_502(
    tmp_path: Path, client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Firmware before 2.5.1 does not use the restart-related 502 behavior."""
    package = tmp_path / "nanokvm_2.5.0.tar.gz"
    package.write_bytes(b"synthetic update package")

    mark_detected(client, application_version="2.5.0")

    mocked.post(f"{BASE_URL}application/update/offline", status=502)
    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        await client.update_application_offline(package)

    assert exc_info.value.status == 502


async def test_update_application_offline_preserves_other_http_errors(
    tmp_path: Path,
    client: NanoKVMClient,
    mocked: aioresponses,
) -> None:
    """Only the restart-related 502 receives special treatment."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")

    mark_detected(client, application_version="2.3.1")

    mocked.post(f"{BASE_URL}application/update/offline", status=500)
    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        await client.update_application_offline(package)

    assert exc_info.value.status == 500


@pytest.mark.parametrize(
    ("hardware", "application_version"),
    [(HWVersion.PRO, "9.9.9"), (HWVersion.PCIE, "2.3.0")],
)
async def test_update_application_offline_rejects_unsupported_devices_before_io(
    tmp_path: Path,
    hardware: HWVersion,
    application_version: str,
    client: NanoKVMClient,
) -> None:
    """The offline update route is non-Pro-only and starts with 2.3.1."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")

    mark_detected(client, hardware, application_version)

    with aioresponses() as mocked, pytest.raises(NanoKVMNotSupportedError):
        await client.update_application_offline(package)

    assert not mocked.requests


async def test_get_update_server_returns_typed_configuration(
    mocked: aioresponses,
) -> None:
    """Custom update-server configuration is returned as a typed model."""
    server_url = (
        "https://synthetic-user:synthetic-password@updates.example.test/nanokvm"
    )

    async with NanoKVMClient(BASE_URL, token="test-token") as client:
        mark_detected(client, application_version="2.5.1")

        mocked.get(
            f"{BASE_URL}application/update-server",
            payload={
                "code": 0,
                "msg": "success",
                "data": {"enabled": True, "url": server_url},
            },
        )
        response = await client.get_update_server()

    assert response.enabled is True
    assert response.url == server_url
    assert server_url not in repr(response)


async def test_set_update_server_posts_and_returns_normalized_configuration(
    caplog: pytest.LogCaptureFixture,
    mocked: aioresponses,
) -> None:
    """The server response is returned without logging URL credentials."""
    caplog.set_level(logging.DEBUG)
    requested_url = "https://synthetic-user:synthetic-password@updates.example.test/"
    normalized_url = requested_url.rstrip("/")
    endpoint = f"{BASE_URL}application/update-server"

    async with NanoKVMClient(BASE_URL, token="test-token") as client:
        mark_detected(client, application_version="2.5.1")

        mocked.post(
            endpoint,
            payload={
                "code": 0,
                "msg": "success",
                "data": {"enabled": True, "url": normalized_url},
            },
        )
        response = await client.set_update_server(True, requested_url)

        call = mocked.requests[("POST", yarl.URL(endpoint))][0]

    assert call.kwargs["json"] == {"enabled": True, "url": requested_url}
    assert response == GetUpdateServerRsp(enabled=True, url=normalized_url)
    assert requested_url not in caplog.text
    assert normalized_url not in caplog.text


def test_update_server_request_hides_url_from_repr() -> None:
    """Credentials embedded in a custom URL are omitted from model reprs."""
    server_url = "https://synthetic-user:synthetic-password@updates.example.test"
    request = SetUpdateServerReq(enabled=True, url=server_url)

    assert request.model_dump() == {"enabled": True, "url": server_url}
    assert server_url not in repr(request)


@pytest.mark.parametrize(
    ("method_name", "args"),
    [("get_update_server", ()), ("set_update_server", (False, ""))],
)
@pytest.mark.parametrize(
    ("hardware", "application_version"),
    [(HWVersion.PRO, "9.9.9"), (HWVersion.PCIE, "2.5.0")],
)
async def test_update_server_methods_reject_unsupported_devices_before_io(
    method_name: str,
    args: tuple[object, ...],
    hardware: HWVersion,
    application_version: str,
    client: NanoKVMClient,
) -> None:
    """Custom servers are available only on non-Pro 2.5.1 and newer."""
    mark_detected(client, hardware, application_version)

    with aioresponses() as mocked, pytest.raises(NanoKVMNotSupportedError):
        await getattr(client, method_name)(*args)

    assert not mocked.requests


async def test_version_check_waits_for_the_update_server(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The device asks its update server while answering, so allow it 60 s."""
    mocked.get(_VERSION_URL, payload=_versions("2.4.3", "2.5.2"))
    mocked.get(_VERSION_URL, payload=_versions("2.4.3", ""))

    assert (await client.get_application_version()).latest == "2.5.2"
    assert (await client.get_application_version(timeout=5)).latest == ""

    calls = _calls(mocked, "GET", _VERSION_URL)
    assert [call.kwargs["timeout"].total for call in calls] == [60, 5]


async def test_update_waits_for_the_installation(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The device installs before answering, so allow it 15 minutes."""
    mark_detected(client, application_version="2.4.3")
    mocked.post(_UPDATE_URL, payload=_ok())
    mocked.post(_UPDATE_URL, payload=_ok())

    await client.update_application()
    mark_detected(client, application_version="2.4.3")
    await client.update_application(timeout=30)

    calls = _calls(mocked, "POST", _UPDATE_URL)
    assert [call.kwargs["timeout"].total for call in calls] == [15 * 60, 30]
    assert client.application_version is None


@pytest.mark.parametrize(("version", "accepted"), [("2.5.1", True), ("2.5.0", False)])
async def test_update_accepts_the_restart_502_from_2_5_1(
    client: NanoKVMClient, mocked: aioresponses, version: str, accepted: bool
) -> None:
    """A proxy 502 during the restart counts as success where it can happen."""
    mark_detected(client, application_version=version)
    mocked.post(_UPDATE_URL, status=502)

    if accepted:
        await client.update_application()
    else:
        with pytest.raises(aiohttp.ClientResponseError):
            await client.update_application()


async def test_install_update_waits_for_the_restart_and_logs_in_again(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The client follows the restart and logs in to the new server."""
    mocked.get(_VERSION_URL, payload=_versions("2.4.3", "2.5.2"))
    # 2.4.3 restarts before answering; the restarted server has a new secret.
    mocked.post(_UPDATE_URL, exception=aiohttp.ServerDisconnectedError())
    mocked.get(_INFO_URL, payload=info_payload("2.4.3"))
    mocked.get(_INFO_URL, exception=aiohttp.ClientConnectionError())
    mocked.get(_INFO_URL, status=401)
    mocked.post(_LOGIN_URL, exception=aiohttp.ClientConnectionError())
    mocked.post(_LOGIN_URL, payload=_ok({"token": "second-token"}))
    mocked.get(_INFO_URL, payload=info_payload("2.5.2", image="v1.4.2"))

    version = await kept_client.install_application_update(poll_interval=0.001)

    assert version == "2.5.2"
    assert kept_client.token == "second-token"
    assert kept_client.application_version == "2.5.2"
    assert kept_client.image_version == "v1.4.2"
    assert len(_calls(mocked, "POST", _LOGIN_URL)) == 3


async def test_install_update_retries_pages_served_while_starting(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A starting server can answer with a page instead of the API's JSON."""
    page = "<html>Starting</html>"
    mocked.get(_VERSION_URL, payload=_versions("2.4.3", "2.5.2"))
    mocked.post(_UPDATE_URL, payload=_ok())
    mocked.get(_INFO_URL, body=page, content_type="text/html")
    mocked.get(_INFO_URL, status=401)
    mocked.post(_LOGIN_URL, body=page, content_type="text/html")
    mocked.post(_LOGIN_URL, payload=_ok({"token": "second-token"}))
    mocked.get(_INFO_URL, payload=info_payload("2.5.2"))

    assert await kept_client.install_application_update(poll_interval=0.001) == "2.5.2"
    assert kept_client.token == "second-token"


async def test_install_update_does_nothing_when_up_to_date(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """An up-to-date device is not updated."""
    mocked.get(_VERSION_URL, payload=_versions("2.5.2", "2.5.2"))

    assert await kept_client.install_application_update() == "2.5.2"
    assert not _calls(mocked, "POST", _UPDATE_URL)


async def test_install_update_needs_the_update_server(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Without a latest version, nothing is installed."""
    mocked.get(_VERSION_URL, payload=_versions("2.4.3", ""))

    with pytest.raises(NanoKVMError, match="update server"):
        await kept_client.install_application_update()
    assert not _calls(mocked, "POST", _UPDATE_URL)


async def test_install_update_needs_kept_credentials(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Without kept credentials, the device is left untouched."""
    with pytest.raises(NanoKVMNotAuthenticatedError, match="keep_credentials"):
        await client.install_application_update()
    assert not mocked.requests


@pytest.mark.parametrize(
    ("timeout", "poll_interval"),
    [(0, 1), (-1, 1), (float("nan"), 1), (60, 0), (60, float("inf"))],
)
async def test_install_update_rejects_invalid_timing(
    kept_client: NanoKVMClient,
    mocked: aioresponses,
    timeout: float,
    poll_interval: float,
) -> None:
    """Invalid timing is rejected before any request."""
    with pytest.raises(ValueError):
        await kept_client.install_application_update(
            timeout=timeout, poll_interval=poll_interval
        )
    assert not _calls(mocked, "GET", _VERSION_URL)


async def test_install_update_reports_a_failed_update(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A failure reported by the device is raised without waiting."""
    mocked.get(_VERSION_URL, payload=_versions("2.4.3", "2.5.2"))
    mocked.post(_UPDATE_URL, payload={"code": -1, "msg": "update failed"})

    with pytest.raises(NanoKVMApiError):
        await kept_client.install_application_update()
    assert not _calls(mocked, "GET", _INFO_URL)


async def test_install_update_stops_when_the_login_is_rejected(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """Rejected credentials after the restart end the wait at once."""
    mocked.get(_VERSION_URL, payload=_versions("2.4.3", "2.5.2"))
    mocked.post(_UPDATE_URL, payload=_ok())
    mocked.get(_INFO_URL, status=401)
    mocked.post(
        _LOGIN_URL,
        payload={
            "code": ApiResponseCode.INVALID_USERNAME_OR_PASSWORD.value,
            "msg": "invalid username or password",
        },
    )

    with pytest.raises(NanoKVMAuthenticationFailure):
        await kept_client.install_application_update(poll_interval=0.001)


async def test_install_update_times_out_when_the_version_stays(
    kept_client: NanoKVMClient, mocked: aioresponses
) -> None:
    """A device that keeps the old version ends the wait with a clear error."""
    mocked.get(_VERSION_URL, payload=_versions("2.4.3", "2.5.2"))
    mocked.post(_UPDATE_URL, payload=_ok())
    mocked.get(_INFO_URL, payload=info_payload("2.4.3"), repeat=True)

    with pytest.raises(TimeoutError, match="within 0.05 seconds"):
        await kept_client.install_application_update(timeout=0.05, poll_interval=0.01)


@pytest.mark.parametrize(
    ("hw_version", "version", "accepted"),
    [
        (HWVersion.PCIE, "2.4.3", True),
        (HWVersion.PCIE, "2.5.0", True),
        (HWVersion.PCIE, "2.5.1", False),
        (HWVersion.PRO, "1.2.14", False),
    ],
)
async def test_update_accepts_the_dropped_connection_before_2_5_1(
    client: NanoKVMClient,
    mocked: aioresponses,
    hw_version: HWVersion,
    version: str,
    accepted: bool,
) -> None:
    """Older non-Pro firmware restarts before its answer is sent."""
    mark_detected(client, hw_version, version)
    mocked.post(_UPDATE_URL, exception=aiohttp.ServerDisconnectedError())

    if accepted:
        await client.update_application()
    else:
        with pytest.raises(aiohttp.ServerDisconnectedError):
            await client.update_application()


async def test_update_detects_the_firmware_before_the_restart(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """The firmware is read first: the device cannot answer after the update."""
    mark_detected(client, HWVersion.PCIE, application_version=None)
    mocked.get(_INFO_URL, payload=info_payload("2.4.3"))
    mocked.post(_UPDATE_URL, exception=aiohttp.ServerDisconnectedError())

    await client.update_application()

    assert len(_calls(mocked, "GET", _INFO_URL)) == 1


@pytest.mark.parametrize(("version", "accepted"), [("2.4.3", True), ("2.5.1", False)])
async def test_offline_update_accepts_the_dropped_connection_before_2_5_1(
    tmp_path: Path,
    client: NanoKVMClient,
    mocked: aioresponses,
    version: str,
    accepted: bool,
) -> None:
    """The offline update handler restarted early before 2.5.1 too."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")
    mark_detected(client, HWVersion.PCIE, version)
    mocked.post(
        f"{BASE_URL}application/update/offline",
        exception=aiohttp.ServerDisconnectedError(),
    )

    if accepted:
        await client.update_application_offline(package)
    else:
        with pytest.raises(aiohttp.ServerDisconnectedError):
            await client.update_application_offline(package)
