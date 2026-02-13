"""Tests for SSL/TLS configuration."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import ssl

from aiohttp import TCPConnector
import pytest
import yarl

from nanokvm.client import NanoKVMClient, NanoKVMSSLError


class TestSSLConfiguration:
    """Test SSL/TLS configuration scenarios."""

    async def test_default_ssl_verification_enabled(self) -> None:
        """Test that SSL verification is enabled by default."""
        client = NanoKVMClient("https://kvm.local/api/")

        ssl_config = client._create_ssl_context()
        assert ssl_config is True

    async def test_ssl_verification_disabled(self) -> None:
        """Test SSL verification can be disabled."""
        client = NanoKVMClient("https://kvm.local/api/", verify_ssl=False)

        ssl_config = client._create_ssl_context()
        assert ssl_config is False

    async def test_custom_ca_certificate(self, tmp_path: Path) -> None:
        """Test custom CA certificate configuration."""
        # Create dummy CA cert file
        ca_cert_file = tmp_path / "ca.pem"
        ca_cert_file.write_text("DUMMY CA CERT")

        with patch("ssl.create_default_context") as mock_ssl_context:
            mock_ctx = MagicMock(spec=ssl.SSLContext)
            mock_ssl_context.return_value = mock_ctx

            client = NanoKVMClient(
                "https://kvm.local/api/", ssl_ca_cert=str(ca_cert_file)
            )

            ssl_config = client._create_ssl_context()

            # Verify create_default_context was called with cafile
            mock_ssl_context.assert_called_once_with(cafile=str(ca_cert_file))
            assert isinstance(ssl_config, MagicMock)

    async def test_nonexistent_ca_cert_raises_error(self) -> None:
        """Test that non-existent CA cert file raises error."""
        client = NanoKVMClient(
            "https://kvm.local/api/", ssl_ca_cert="/path/that/does/not/exist.pem"
        )

        with pytest.raises(NanoKVMSSLError, match="CA certificate not found"):
            client._create_ssl_context()

    async def test_http_url_works_regardless_of_ssl_config(self) -> None:
        """Test that HTTP URL (not HTTPS) works regardless of SSL config."""
        client = NanoKVMClient("http://kvm.local/api/", verify_ssl=False)

        async with client:
            assert client._session is not None

    async def test_session_created_with_tcp_connector(self) -> None:
        """Test that session is created with TCPConnector."""
        client = NanoKVMClient("https://kvm.local/api/")

        async with client:
            assert client._session is not None
            assert client._session.connector is not None


class TestPasswordObfuscation:
    """Test password obfuscation modes."""

    async def test_password_obfuscation_enabled_by_default(self) -> None:
        """Test that password obfuscation is enabled by default (backward compatibility)."""
        client = NanoKVMClient("https://kvm.local/api/")
        assert client._use_password_obfuscation is True

    async def test_password_obfuscation_can_be_disabled(self) -> None:
        """Test that password obfuscation can be disabled."""
        client = NanoKVMClient(
            "https://kvm.local/api/", use_password_obfuscation=False
        )
        assert client._use_password_obfuscation is False

    async def test_authenticate_with_plain_text_password(self) -> None:
        """Test authentication with plain text password (newer NanoKVM)."""
        from aioresponses import aioresponses

        async with NanoKVMClient(
            "https://kvm.local/api/", use_password_obfuscation=False
        ) as client:
            with aioresponses() as m:
                # Mock login endpoint
                m.post(
                    "https://kvm.local/api/auth/login",
                    payload={"code": 0, "msg": "success", "data": {"token": "abc123"}},
                )

                await client.authenticate("root", "password123")

                # Verify the request was made with plain text password
                calls = m.requests[("POST", yarl.URL("https://kvm.local/api/auth/login"))]
                assert len(calls) == 1
                request_json = calls[0].kwargs.get("json")
                assert request_json["username"] == "root"
                assert request_json["password"] == "password123"  # Plain text!

    async def test_authenticate_with_obfuscated_password(self) -> None:
        """Test authentication with obfuscated password (older NanoKVM)."""
        from aioresponses import aioresponses

        async with NanoKVMClient(
            "https://kvm.local/api/", use_password_obfuscation=True
        ) as client:
            with aioresponses() as m:
                # Mock login endpoint
                m.post(
                    "https://kvm.local/api/auth/login",
                    payload={"code": 0, "msg": "success", "data": {"token": "abc123"}},
                )

                await client.authenticate("root", "password123")

                # Verify the request was made with obfuscated password
                calls = m.requests[("POST", yarl.URL("https://kvm.local/api/auth/login"))]
                assert len(calls) == 1
                request_json = calls[0].kwargs.get("json")
                assert request_json["username"] == "root"
                # Should be obfuscated (starts with "U2FsdGVkX1")
                assert request_json["password"].startswith("U2FsdGVkX1")
                assert request_json["password"] != "password123"  # Not plain text


class TestSSLIntegration:
    """Integration tests for SSL scenarios."""

    async def test_full_client_lifecycle_with_ssl(self) -> None:
        """Test full client lifecycle with SSL configuration."""
        from aioresponses import aioresponses

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
