"""API client for NanoKVM."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientSession
import yarl

from . import models
from .compatibility import require_application_version, require_hardware
from .components.hid import PASTE_CHAR_MAP, HidController
from .components.mouse import MouseController
from .components.network import NetworkController
from .components.services import ServiceController
from .components.session import SessionController
from .components.storage import ImageTransferProgressCallback, StorageController
from .components.stream import StreamController
from .components.system import SystemController
from .components.video import VideoController
from .exceptions import (
    NanoKVMApiError,
    NanoKVMAuthenticationFailure,
    NanoKVMError,
    NanoKVMInvalidResponseError,
    NanoKVMNotAuthenticatedError,
    NanoKVMNotSupportedError,
    NanoKVMPermissionError,
)
from .models import *  # noqa: F403
from .models import GetAccountRsp, HWVersion, IsPasswordUpdatedRsp
from .utils import obfuscate_password

__all__ = [
    "ImageTransferProgressCallback",
    "MouseController",
    "NanoKVMApiError",
    "NanoKVMAuthenticationFailure",
    "NanoKVMClient",
    "NanoKVMError",
    "NanoKVMInvalidResponseError",
    "NanoKVMNotAuthenticatedError",
    "NanoKVMNotSupportedError",
    "NanoKVMPermissionError",
    "PASTE_CHAR_MAP",
    "SessionController",
    "StreamController",
    "obfuscate_password",
    "require_application_version",
    "require_hardware",
]
__all__ += models.__all__


class NanoKVMClient(
    SystemController,
    HidController,
    VideoController,
    StorageController,
    NetworkController,
    ServiceController,
    StreamController,
    MouseController,
):
    """Async API client for the NanoKVM.

    Endpoint methods are inherited from the domain controllers in
    ``nanokvm.components``; this class owns the shared session and
    authentication flow.
    """

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        request_timeout: int = 10,
        session: ClientSession | None = None,
        verify_ssl: bool = True,
        ssl_ca_cert: str | None = None,
        ssl_fingerprint: str | None = None,
        use_password_obfuscation: bool | None = None,
    ) -> None:
        """
        Initialize the NanoKVM client.

        Args:
            url: Base URL of the NanoKVM API (e.g., "https://kvm.local/api/")
            session: aiohttp ClientSession to use for requests.
            token: Optional pre-existing authentication token
            request_timeout: Request timeout in seconds (default: 10)
            verify_ssl: Enable SSL certificate verification (default: True).
                Set to False to disable verification for self-signed certificates.
            ssl_ca_cert: Path to custom CA certificate bundle file for SSL verification.
                Useful for self-signed certificates or private CAs.
            ssl_fingerprint: SHA-256 fingerprint of the server's TLS certificate
                as a hex string. When set, the client will verify the server's
                certificate fingerprint instead of performing CA-based verification.
                Use `async_fetch_remote_fingerprint()` to retrieve this value.
            use_password_obfuscation: Control password obfuscation mode (default: None).
                None = auto-detect (try obfuscated first, fall back to plain text).
                True = always use obfuscated passwords (older NanoKVM versions).
                False = always use plain text passwords (newer HTTPS-enabled versions).
        """
        super().__init__(
            SessionController(
                url,
                token=token,
                request_timeout=request_timeout,
                session=session,
                verify_ssl=verify_ssl,
                ssl_ca_cert=ssl_ca_cert,
                ssl_fingerprint=ssl_fingerprint,
                use_password_obfuscation=use_password_obfuscation,
            )
        )

    @property
    def url(self) -> yarl.URL:
        """Return the device API URL."""
        return self._session.url

    @url.setter
    def url(self, value: yarl.URL) -> None:
        self._session.url = value

    @property
    def token(self) -> str | None:
        """Return the current auth token."""
        return self._session._token

    @property
    def hw_version(self) -> HWVersion | None:
        """The detected hardware version. None if not yet detected."""
        return self._session._hw_version

    @property
    def application_version(self) -> str | None:
        """The detected application version. None if not yet detected."""
        return self._session._application_version

    @property
    def image_version(self) -> str | None:
        """The detected image version. None if not yet detected."""
        return self._session._image_version

    async def detect_hardware(self) -> None:
        """Detect and store the hardware version."""
        await self._session.detect_hardware()

    async def detect_versions(self) -> None:
        """Detect and store image and application versions."""
        await self._session.detect_versions()

    async def __aenter__(self) -> NanoKVMClient:
        """Async context manager entry."""
        await self._session.enter()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - cleanup resources."""
        await self._session.exit()

    # ── Authentication ──────────────────────────────────────────────────

    async def authenticate(self, username: str, password: str) -> None:
        """Authenticate and store the session token."""
        generation = await self._session.authenticate(username, password)
        await self.detect_hardware()
        self._session.check_session_generation(generation)

    async def logout(self) -> None:
        """Log out and clear the session token."""
        await self._session.logout()

    async def change_password(
        self,
        username: str,
        new_password: str,
        *,
        current_password: str | None = None,
    ) -> None:
        """Change the KVM password for the authenticated account."""
        await self._session.change_password(
            username,
            new_password,
            current_password=current_password,
        )

    async def is_password_updated(self) -> IsPasswordUpdatedRsp:
        """Check if the default password has been changed."""
        return await self._session.is_password_updated()

    async def get_account(self) -> GetAccountRsp:
        """Get the configured username."""
        return await self._session.get_account()
