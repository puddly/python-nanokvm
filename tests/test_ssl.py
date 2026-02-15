"""Tests for SSL/TLS configuration and password obfuscation."""

from pathlib import Path
import ssl
from unittest.mock import MagicMock, patch

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMAuthenticationFailure, NanoKVMClient


async def test_default_ssl_verification_enabled() -> None:
    """Test that SSL verification is enabled by default."""
    client = NanoKVMClient("https://kvm.local/api/")

    ssl_config = client._create_ssl_context()
    assert ssl_config is True


async def test_ssl_verification_disabled() -> None:
    """Test SSL verification can be disabled."""
    client = NanoKVMClient("https://kvm.local/api/", verify_ssl=False)

    ssl_config = client._create_ssl_context()
    assert ssl_config is False


async def test_custom_ca_certificate(tmp_path: Path) -> None:
    """Test custom CA certificate configuration."""
    ca_cert_file = tmp_path / "ca.pem"
    ca_cert_file.write_text("DUMMY CA CERT")

    with patch("ssl.create_default_context") as mock_ssl_context:
        mock_ctx = MagicMock(spec=ssl.SSLContext)
        mock_ssl_context.return_value = mock_ctx

        client = NanoKVMClient(
            "https://kvm.local/api/", ssl_ca_cert=str(ca_cert_file)
        )

        ssl_config = client._create_ssl_context()

        mock_ssl_context.assert_called_once_with(cafile=str(ca_cert_file))
        assert isinstance(ssl_config, MagicMock)


async def test_nonexistent_ca_cert_raises_error() -> None:
    """Test that non-existent CA cert file raises FileNotFoundError."""
    client = NanoKVMClient(
        "https://kvm.local/api/", ssl_ca_cert="/path/that/does/not/exist.pem"
    )

    with pytest.raises(FileNotFoundError):
        client._create_ssl_context()


async def test_http_url_works_regardless_of_ssl_config() -> None:
    """Test that HTTP URL (not HTTPS) works regardless of SSL config."""
    client = NanoKVMClient("http://kvm.local/api/", verify_ssl=False)

    async with client:
        assert client._session is not None


async def test_session_created_with_tcp_connector() -> None:
    """Test that session is created with TCPConnector."""
    client = NanoKVMClient("https://kvm.local/api/")

    async with client:
        assert client._session is not None
        assert client._session.connector is not None


async def test_password_obfuscation_auto_by_default() -> None:
    """Test that password obfuscation defaults to None (auto-detect)."""
    client = NanoKVMClient("https://kvm.local/api/")
    assert client._use_password_obfuscation is None


async def test_password_obfuscation_can_be_disabled() -> None:
    """Test that password obfuscation can be disabled."""
    client = NanoKVMClient(
        "https://kvm.local/api/", use_password_obfuscation=False
    )
    assert client._use_password_obfuscation is False


async def test_authenticate_with_plain_text_password() -> None:
    """Test authentication with plain text password (newer NanoKVM)."""
    async with NanoKVMClient(
        "https://kvm.local/api/", use_password_obfuscation=False
    ) as client:
        with aioresponses() as m:
            m.post(
                "https://kvm.local/api/auth/login",
                payload={"code": 0, "msg": "success", "data": {"token": "abc123"}},
            )

            await client.authenticate("root", "password123")

            calls = m.requests[("POST", yarl.URL("https://kvm.local/api/auth/login"))]
            assert len(calls) == 1
            request_json = calls[0].kwargs.get("json")
            assert request_json["username"] == "root"
            assert request_json["password"] == "password123"


async def test_authenticate_with_obfuscated_password() -> None:
    """Test authentication with obfuscated password (older NanoKVM)."""
    async with NanoKVMClient(
        "https://kvm.local/api/", use_password_obfuscation=True
    ) as client:
        with aioresponses() as m:
            m.post(
                "https://kvm.local/api/auth/login",
                payload={"code": 0, "msg": "success", "data": {"token": "abc123"}},
            )

            await client.authenticate("root", "password123")

            calls = m.requests[("POST", yarl.URL("https://kvm.local/api/auth/login"))]
            assert len(calls) == 1
            request_json = calls[0].kwargs.get("json")
            assert request_json["username"] == "root"
            assert request_json["password"].startswith("U2FsdGVkX1")
            assert request_json["password"] != "password123"


async def test_auto_detect_obfuscation_succeeds() -> None:
    """Test auto-detect succeeds with obfuscated password on first attempt."""
    async with NanoKVMClient("https://kvm.local/api/") as client:
        with aioresponses() as m:
            m.post(
                "https://kvm.local/api/auth/login",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {"token": "abc123"},
                },
            )

            await client.authenticate("root", "password123")

            calls = m.requests[
                ("POST", yarl.URL("https://kvm.local/api/auth/login"))
            ]
            assert len(calls) == 1
            request_json = calls[0].kwargs.get("json")
            assert request_json["password"].startswith("U2FsdGVkX1")
            assert client.token == "abc123"


async def test_auto_detect_fallback_to_plain_text() -> None:
    """Test auto-detect falls back to plain text after obfuscated fails."""
    async with NanoKVMClient("https://kvm.local/api/") as client:
        with aioresponses() as m:
            # First call: obfuscated fails with code -2
            m.post(
                "https://kvm.local/api/auth/login",
                payload={
                    "code": -2,
                    "msg": "invalid username or password",
                    "data": None,
                },
            )
            # Second call: plain text succeeds
            m.post(
                "https://kvm.local/api/auth/login",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {"token": "abc123"},
                },
            )

            await client.authenticate("root", "password123")

            calls = m.requests[
                ("POST", yarl.URL("https://kvm.local/api/auth/login"))
            ]
            assert len(calls) == 2
            # First attempt: obfuscated
            assert calls[0].kwargs.get("json")[
                "password"
            ].startswith("U2FsdGVkX1")
            # Second attempt: plain text
            assert (
                calls[1].kwargs.get("json")["password"]
                == "password123"
            )
            assert client.token == "abc123"


async def test_auto_detect_both_fail() -> None:
    """Test auto-detect raises NanoKVMAuthenticationFailure when both fail."""
    async with NanoKVMClient("https://kvm.local/api/") as client:
        with aioresponses() as m:
            # First call: obfuscated fails
            m.post(
                "https://kvm.local/api/auth/login",
                payload={
                    "code": -2,
                    "msg": "invalid username or password",
                    "data": None,
                },
            )
            # Second call: plain text also fails
            m.post(
                "https://kvm.local/api/auth/login",
                payload={
                    "code": -2,
                    "msg": "invalid username or password",
                    "data": None,
                },
            )

            with pytest.raises(NanoKVMAuthenticationFailure):
                await client.authenticate("root", "wrong_password")


async def test_full_client_lifecycle_with_ssl() -> None:
    """Test full client lifecycle with SSL configuration."""
    async with NanoKVMClient(
        "https://kvm.local/api/", token="test-token", verify_ssl=True
    ) as client:
        with aioresponses() as m:
            m.get(
                "https://kvm.local/api/vm/info",
                payload={
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "ips": [
                            {
                                "name": "eth0",
                                "addr": "192.168.1.100",
                                "version": "4",
                                "type": "ethernet",
                            }
                        ],
                        "mdns": "kvm.local",
                        "image": "v1.0.0",
                        "application": "v2.1.0",
                        "deviceKey": "abc123",
                    },
                },
            )

            info = await client.get_info()
            assert len(info.ips) == 1
            assert info.ips[0].addr == "192.168.1.100"
