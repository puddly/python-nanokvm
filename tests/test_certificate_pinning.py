"""Integration test for TLS certificate pinning with a real HTTPS server."""

from collections.abc import AsyncGenerator
import datetime
import ipaddress
import json
import pathlib
import ssl

import aiohttp
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from nanokvm.client import NanoKVMClient
from nanokvm.utils import async_fetch_remote_fingerprint


def generate_nanokvm_cert() -> tuple[bytes, bytes]:
    """Generate a self-signed certificate matching NanoKVM's cert.go parameters.

    RSA 2048, CN=localhost, SAN: localhost + 127.0.0.1 + ::1, valid 10 years,
    KeyUsage: keyEncipherment | digitalSignature, ExtKeyUsage: serverAuth.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=365 * 10)
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                    x509.IPAddress(ipaddress.IPv6Address("::1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    return cert_pem, key_pem


async def _handle_hardware(_request: web.Request) -> web.Response:
    return web.Response(
        text=json.dumps({"code": 0, "msg": "success", "data": {"version": "Alpha"}}),
        content_type="application/json",
    )


async def _handle_login(request: web.Request) -> web.Response:
    body = await request.json()

    if body["username"] == "admin" and body["password"] == "test":
        return web.Response(
            text=json.dumps(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {"token": "fake-token-123"},
                }
            ),
            content_type="application/json",
        )

    return web.Response(
        text=json.dumps(
            {
                "code": -2,
                "msg": "invalid username or password",
                "data": None,
            }
        ),
        content_type="application/json",
    )


@pytest.fixture
async def nanokvm_https_server(tmp_path: pathlib.Path) -> AsyncGenerator[str, None]:
    """Spin up a minimal HTTPS server mimicking a NanoKVM device."""
    cert_pem, key_pem = generate_nanokvm_cert()

    cert_file = tmp_path / "server.crt"
    key_file = tmp_path / "server.key"
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(key_pem)

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(str(cert_file), str(key_file))

    app = web.Application()
    app.router.add_post("/api/auth/login", _handle_login)
    app.router.add_get("/api/vm/hardware", _handle_hardware)

    runner = web.AppRunner(app)
    await runner.setup()

    try:
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
        await site.start()

        host, port = runner.addresses[0]
        yield f"https://{host}:{port}/api/"
    finally:
        await runner.cleanup()


async def test_certificate_pinning(nanokvm_https_server: str) -> None:
    """Test the full certificate pinning flow against a real HTTPS server.

    1. Connecting with default SSL verification fails (self-signed cert).
    2. Fetch the server's certificate fingerprint.
    3. Connecting with the pinned fingerprint succeeds.
    """
    url = nanokvm_https_server

    # Step 1: default SSL verification rejects the self-signed certificate
    async with NanoKVMClient(url, use_password_obfuscation=False) as client:
        with pytest.raises(aiohttp.ClientConnectorCertificateError):
            await client.authenticate("admin", "test")

    # Step 2: fetch the remote certificate fingerprint
    fingerprint = await async_fetch_remote_fingerprint(url)
    assert len(fingerprint) == 64  # SHA-256 hex string

    # Step 3: pinned fingerprint allows the connection to succeed
    async with NanoKVMClient(
        url,
        ssl_fingerprint=fingerprint,
        use_password_obfuscation=False,
    ) as client:
        await client.authenticate("admin", "test")
        assert client.token == "fake-token-123"


async def test_certificate_pinning_colon_separated(nanokvm_https_server: str) -> None:
    """Test that colon-separated fingerprints (e.g. from openssl) are accepted."""
    url = nanokvm_https_server
    fingerprint = await async_fetch_remote_fingerprint(url)

    # Convert "AABB..." to "AA:BB:..."
    colon_fingerprint = ":".join(
        fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2)
    )

    async with NanoKVMClient(
        url,
        ssl_fingerprint=colon_fingerprint,
        use_password_obfuscation=False,
    ) as client:
        await client.authenticate("admin", "test")
        assert client.token == "fake-token-123"


async def test_certificate_pinning_wrong_hash(nanokvm_https_server: str) -> None:
    """Test that a wrong pinned hash is rejected."""
    url = nanokvm_https_server

    async with NanoKVMClient(
        url,
        ssl_fingerprint="AB" * 32,
        use_password_obfuscation=False,
    ) as client:
        with pytest.raises(aiohttp.ServerFingerprintMismatch):
            await client.authenticate("admin", "test")
