"""Exceptions raised by the NanoKVM client."""

from __future__ import annotations

from typing import Any


class NanoKVMError(Exception):
    """Base exception for NanoKVM client errors."""


class NanoKVMNotAuthenticatedError(NanoKVMError):
    """Exception for authentication errors."""


class NanoKVMPermissionError(NanoKVMError):
    """Exception for an authenticated request forbidden by the device."""

    def __init__(
        self,
        message: str = "NanoKVM permission denied",
        *,
        status: int = 403,
        method: str,
        path: str,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.method = method.upper()
        self.path = path


class NanoKVMApiError(NanoKVMError):
    """Exception for API-level errors reported by the device."""

    def __init__(self, message: str, code: int, msg: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.msg = msg
        self.data = data


class NanoKVMAuthenticationFailure(NanoKVMError):
    """Exception for authentication failure."""


class NanoKVMInvalidResponseError(NanoKVMError):
    """Exception for unexpected or unparsable responses."""


class NanoKVMNotSupportedError(NanoKVMError):
    """Feature not supported on this hardware variant."""
