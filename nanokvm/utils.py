import asyncio
import base64
import hashlib
import os
import ssl
import urllib.parse

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# XXX: this offers no security whatsoever, since this is a symmetric cipher with a
# PUBLIC key. This is no more or less secure than ROT13.
SECRET_KEY = b"nanokvm-sipeed-2024"


def evp_bytes_to_key_aes256_md5(
    password: bytes, salt: bytes, *, key_len: int = 32, iv_len: int = 16
) -> tuple[bytes, bytes]:
    """OpenSSL's `EVP_BytesToKey` function with a few hardcoded parameters."""
    derived = b""
    block = b""

    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + password + salt).digest()
        derived += block

    key = derived[0:key_len]
    iv = derived[key_len : key_len + iv_len]

    return key, iv


def openssl_encrypt_aes256cbc_md5(plaintext: bytes, password: bytes) -> bytes:
    """Implementation of `CryptoJS.AES.encrypt`."""

    salt = os.urandom(8)
    key, iv = evp_bytes_to_key_aes256_md5(password, salt)

    padder = padding.PKCS7(128).padder()
    padded_plaintext = padder.update(plaintext) + padder.finalize()

    cipher = Cipher(algorithms.AES256(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext: bytes = encryptor.update(padded_plaintext) + encryptor.finalize()

    return b"Salted__" + salt + ciphertext


def obfuscate_password(password: str) -> str:
    """Obfuscate a password."""
    password_enc = openssl_encrypt_aes256cbc_md5(
        plaintext=password.encode("utf-8"),
        password=SECRET_KEY,
    )

    return urllib.parse.quote(base64.b64encode(password_enc).decode("utf-8"), safe="")


async def async_fetch_remote_fingerprint(
    url: str, *, timeout: float | None = 10.0
) -> str:
    """Retrieve the SHA-256 fingerprint of the remote server's TLS certificate.

    Connects to the server with verification disabled to grab the raw certificate,
    then returns its SHA-256 hash as an uppercase hex string.

    This is useful for establishing an initial trust-on-first-use pin with
    `NanoKVMClient(url, ssl_fingerprint=...)`.
    """
    parsed_url = urllib.parse.urlparse(url)
    hostname = parsed_url.hostname
    port = parsed_url.port or 443

    ssl_ctx = await asyncio.to_thread(ssl.create_default_context)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with asyncio.timeout(timeout):
        _, writer = await asyncio.open_connection(hostname, port, ssl=ssl_ctx)

    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        der_cert = ssl_obj.getpeercert(binary_form=True)
        return hashlib.sha256(der_cert).hexdigest().upper()
    finally:
        writer.close()
        await writer.wait_closed()
