"""Tests for offline and custom-server application updates."""

import hashlib
import logging
from pathlib import Path

import aiohttp
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMClient, NanoKVMNotSupportedError
from nanokvm.models import GetUpdateServerRsp, HWVersion, SetUpdateServerReq

_BASE_URL = "http://localhost:8888/api/"


def _mark_non_pro(
    client: NanoKVMClient,
    *,
    application_version: str,
) -> None:
    client._session._hw_version = HWVersion.PCIE
    client._session._application_version = application_version


async def test_update_application_offline_uploads_package_and_checksum(
    tmp_path: Path,
) -> None:
    """Offline updates use the firmware multipart field and checksum header."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    url = f"{_BASE_URL}application/update/offline"

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.5.1")

        with aioresponses() as mocked:
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
) -> None:
    """Legacy firmware gets local checksum protection without an ignored header."""
    package = tmp_path / "nanokvm_2.5.0.tar.gz"
    package.write_bytes(b"synthetic update package")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    url = f"{_BASE_URL}application/update/offline"

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.5.0")

        with aioresponses() as mocked:
            mocked.post(
                url,
                payload={"code": 0, "msg": "success", "data": None},
            )

            await client.update_application_offline(package, sha256=checksum)

        call = mocked.requests[("POST", yarl.URL(url))][0]
        assert "X-SHA256-Checksum" not in call.kwargs["headers"]


async def test_update_application_offline_rejects_checksum_mismatch_before_io(
    tmp_path: Path,
) -> None:
    """A mismatched package checksum fails before every supported upload contract."""
    package = tmp_path / "nanokvm_2.5.0.tar.gz"
    package.write_bytes(b"synthetic update package")

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.3.1")

        with (
            aioresponses() as mocked,
            pytest.raises(ValueError, match="does not match"),
        ):
            await client.update_application_offline(package, sha256="a" * 64)

        assert not mocked.requests


async def test_update_application_offline_omits_empty_checksum(tmp_path: Path) -> None:
    """An omitted checksum does not send an empty checksum header."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")
    url = f"{_BASE_URL}application/update/offline"

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.3.1")

        with aioresponses() as mocked:
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
) -> None:
    """Malformed checksums fail before a package is uploaded."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.3.1")

        with aioresponses() as mocked, pytest.raises(ValueError, match="SHA-256"):
            await client.update_application_offline(package, sha256=sha256)

        assert not mocked.requests


async def test_update_application_offline_rejects_invalid_filename_before_io(
    tmp_path: Path,
) -> None:
    """Only official NanoKVM application package names are accepted."""
    package = tmp_path / "update.tar.gz"
    package.write_bytes(b"synthetic update package")

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.3.1")

        with aioresponses() as mocked, pytest.raises(ValueError, match="filename"):
            await client.update_application_offline(package)

        assert not mocked.requests


async def test_update_application_offline_accepts_restart_502(tmp_path: Path) -> None:
    """A proxy 502 after the upload matches the official UI success handling."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.5.1")

        with aioresponses() as mocked:
            mocked.post(f"{_BASE_URL}application/update/offline", status=502)
            await client.update_application_offline(package)


async def test_update_application_offline_preserves_legacy_502(tmp_path: Path) -> None:
    """Firmware before 2.5.1 does not use the restart-related 502 behavior."""
    package = tmp_path / "nanokvm_2.5.0.tar.gz"
    package.write_bytes(b"synthetic update package")

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.5.0")

        with aioresponses() as mocked:
            mocked.post(f"{_BASE_URL}application/update/offline", status=502)
            with pytest.raises(aiohttp.ClientResponseError) as exc_info:
                await client.update_application_offline(package)

        assert exc_info.value.status == 502


async def test_update_application_offline_preserves_other_http_errors(
    tmp_path: Path,
) -> None:
    """Only the restart-related 502 receives special treatment."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.3.1")

        with aioresponses() as mocked:
            mocked.post(f"{_BASE_URL}application/update/offline", status=500)
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
) -> None:
    """The offline update route is non-Pro-only and starts with 2.3.1."""
    package = tmp_path / "nanokvm_2.5.2.tar.gz"
    package.write_bytes(b"synthetic update package")

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = hardware
        client._session._application_version = application_version

        with aioresponses() as mocked, pytest.raises(NanoKVMNotSupportedError):
            await client.update_application_offline(package)

        assert not mocked.requests


async def test_get_update_server_returns_typed_configuration() -> None:
    """Custom update-server configuration is returned as a typed model."""
    server_url = (
        "https://synthetic-user:synthetic-password@updates.example.test/nanokvm"
    )

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.5.1")

        with aioresponses() as mocked:
            mocked.get(
                f"{_BASE_URL}application/update-server",
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
) -> None:
    """The server response is returned without logging URL credentials."""
    caplog.set_level(logging.DEBUG)
    requested_url = "https://synthetic-user:synthetic-password@updates.example.test/"
    normalized_url = requested_url.rstrip("/")
    endpoint = f"{_BASE_URL}application/update-server"

    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        _mark_non_pro(client, application_version="2.5.1")

        with aioresponses() as mocked:
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
) -> None:
    """Custom servers are available only on non-Pro 2.5.1 and newer."""
    async with NanoKVMClient(_BASE_URL, token="test-token") as client:
        client._session._hw_version = hardware
        client._session._application_version = application_version

        with aioresponses() as mocked, pytest.raises(NanoKVMNotSupportedError):
            await getattr(client, method_name)(*args)

        assert not mocked.requests
