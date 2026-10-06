"""Hardware and firmware compatibility checks for client operations."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
import functools
import re
from typing import TYPE_CHECKING, Any, TypeVar

from .exceptions import NanoKVMError, NanoKVMNotSupportedError
from .models.common import HWFamily, HWVersion

if TYPE_CHECKING:
    from .components.session import Controller


F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")


def _parse_version(version: str) -> tuple[int, ...] | None:
    """Parse simple semantic app versions; return None for custom/dev builds."""
    match = _VERSION_RE.fullmatch(version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _version_at_least(version: str, minimum: str) -> bool:
    """Return True when version is unknown or at least the requested version."""
    parsed_version = _parse_version(version)
    parsed_minimum = _parse_version(minimum)
    if parsed_version is None or parsed_minimum is None:
        return True

    length = max(len(parsed_version), len(parsed_minimum))
    normalized_version = parsed_version + (0,) * (length - len(parsed_version))
    normalized_minimum = parsed_minimum + (0,) * (length - len(parsed_minimum))
    return normalized_version >= normalized_minimum


def require_hardware(*requirements: HWVersion | HWFamily) -> Callable[[F], F]:
    """Restrict a method to specific hardware versions or families."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(self: Controller, *args: Any, **kwargs: Any) -> Any:
            if self._session._hw_version is None:
                if self._session._token is None:
                    raise NanoKVMError(
                        f"{func.__name__} requires hardware detection; "
                        f"authenticate or call detect_hardware() first"
                    )
                await self._session.detect_hardware()
            assert self._session._hw_version is not None
            family = self._session._hw_version.family
            matches = any(
                (isinstance(requirement, HWFamily) and family is requirement)
                or (
                    isinstance(requirement, HWVersion)
                    and self._session._hw_version is requirement
                )
                for requirement in requirements
            )
            if not matches:
                allowed = ", ".join(requirement.value for requirement in requirements)
                raise NanoKVMNotSupportedError(
                    f"{func.__name__} requires hardware: {allowed} "
                    f"(detected: {self._session._hw_version})"
                )
            return await func(self, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def require_application_version(
    *,
    non_pro: str | None = None,
    pro: str | None = None,
) -> Callable[[F], F]:
    """Decorator that restricts a method to minimum application versions."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(self: Controller, *args: Any, **kwargs: Any) -> Any:
            if not await self._session.application_version_at_least(
                non_pro=non_pro, pro=pro
            ):
                is_pro = self._session.is_hardware_family(HWFamily.PRO)
                raise NanoKVMNotSupportedError(
                    f"{func.__name__} requires {'Pro' if is_pro else 'non-Pro'} "
                    f"application version >= {pro if is_pro else non_pro} "
                    f"(detected: {self._session._application_version})"
                )

            return await func(self, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
