import base64
import hashlib
import os
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
