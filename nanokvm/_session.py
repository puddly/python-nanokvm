"""Internal HTTP, authentication, and session handling for NanoKVM."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
from http.cookies import Morsel
import json
import logging
from os import PathLike
from pathlib import Path
import ssl
from typing import TYPE_CHECKING, Any, TypeVar

import aiohttp
from aiohttp import ClientResponse, ClientSession, Fingerprint, hdrs
from pydantic import BaseModel, ValidationError

from .models.common import (
    ApiResponse,
    ApiResponseCode,
    ChangePasswordReq,
    ChangePasswordV251Req,
    HWFamily,
    LoginReq,
    LoginRsp,
)

if TYPE_CHECKING:
    from .client import NanoKVMClient


T = TypeVar("T", bound=BaseModel)


class _SessionController:
    """Own HTTP requests and the authentication state of a client."""

    def __init__(self, client: NanoKVMClient, logger: logging.Logger) -> None:
        self._client = client
        self._logger = logger

    def create_ssl_context(self) -> ssl.SSLContext | Fingerprint | bool:
        """Create and configure SSL context from the client's settings."""
        client = self._client
        if client._ssl_fingerprint:
            self._logger.debug("Using certificate fingerprint pinning")
            return Fingerprint(bytes.fromhex(client._ssl_fingerprint.replace(":", "")))

        if not client._verify_ssl:
            self._logger.warning(
                "SSL verification is disabled. This is insecure and should only be "
                "used for testing with self-signed certificates."
            )
            return False

        if not client._ssl_ca_cert:
            return True

        ssl_ctx = ssl.create_default_context(cafile=client._ssl_ca_cert)
        self._logger.debug("Using custom CA certificate: %s", client._ssl_ca_cert)
        return ssl_ctx

    async def enter(self) -> None:
        """Initialize the client's SSL configuration and HTTP session."""
        client = self._client
        client._ssl_config = await asyncio.to_thread(self.create_ssl_context)
        if client._session is None and not client._external_session_provided:
            client._session = ClientSession()

    async def exit(self) -> None:
        """Close the client's WebSocket and owned HTTP resources."""
        client = self._client
        await client._close_ws()

        if client._ws_session is not None:
            await client._ws_session.close()
            client._ws_session = None

        if client._session is not None and not client._external_session_provided:
            await client._session.close()
            client._session = None

    @contextlib.asynccontextmanager
    async def request(
        self,
        method: str,
        path: str,
        *,
        authenticate: bool = True,
        timeout: aiohttp.ClientTimeout | None = None,
        expected_generation: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ClientResponse]:
        """Make an authenticated or unauthenticated API request."""
        from .client import NanoKVMNotAuthenticatedError, NanoKVMPermissionError

        client = self._client
        if expected_generation is not None:
            client._check_session_generation(expected_generation)
        generation = client._session_generation
        cookies = {}
        if authenticate:
            if not client._token:
                raise NanoKVMNotAuthenticatedError("Client is not authenticated")
            cookies["nano-kvm-token"] = client._token

        assert client._session is not None
        assert client._ssl_config is not None

        client._clear_session_cookies()
        request_headers = {
            hdrs.ACCEPT: "application/json",
            **kwargs.pop("headers", {}),
        }

        async with client._session.request(
            method,
            client.url / path.lstrip("/"),
            headers=request_headers,
            cookies=cookies,
            timeout=timeout or aiohttp.ClientTimeout(total=client._request_timeout),
            raise_for_status=False,
            ssl=client._ssl_config,
            **kwargs,
        ) as response:
            # The explicit token owns the session. Do not leave response cookies
            # available to another client sharing this HTTP session.
            client._clear_session_cookies()
            if authenticate and response.status == 401:
                await client._clear_local_session(expected_generation=generation)
                raise NanoKVMNotAuthenticatedError(
                    "NanoKVM session is no longer authenticated"
                )
            if authenticate and response.status == 403:
                raise NanoKVMPermissionError(
                    status=response.status,
                    method=response.method,
                    path=f"/{path.lstrip('/')}",
                )
            response.raise_for_status()
            yield response

    async def read_json_response(self, response: ClientResponse) -> Any:
        """Read a JSON response and normalize decoding failures."""
        try:
            return await response.json(content_type=None)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            from .client import NanoKVMInvalidResponseError

            raise NanoKVMInvalidResponseError(
                "Invalid JSON response received"
            ) from None

    async def api_request_json(
        self,
        method: str,
        path: str,
        response_model: type[T] | None = None,
        data: BaseModel | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Make an API request and parse its JSON response."""
        client = self._client
        self._logger.debug("Making API request: %s %s", method, path)

        async with client._request(
            method,
            path,
            json=(
                data.model_dump(by_alias=True, exclude_none=True)
                if data is not None
                else None
            ),
            **kwargs,
        ) as response:
            raw_response = await client._read_json_response(response)

        return client._validate_api_response(raw_response, response_model)

    async def api_request_form(
        self,
        method: str,
        path: str,
        response_model: type[T] | None = None,
        data: aiohttp.FormData | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Make a multipart/form request and parse its JSON response."""
        client = self._client
        self._logger.debug("Making API form request: %s %s", method, path)

        async with client._request(method, path, data=data, **kwargs) as response:
            raw_response = await client._read_json_response(response)

        return client._validate_api_response(raw_response, response_model)

    def validate_api_response(
        self,
        raw_response: Any,
        response_model: type[T] | None = None,
    ) -> T | None:
        """Validate the shared NanoKVM response envelope."""
        from .client import NanoKVMApiError, NanoKVMInvalidResponseError

        try:
            api_response = ApiResponse[Any].model_validate(raw_response)
        except ValidationError:
            raise NanoKVMInvalidResponseError("Invalid API response envelope") from None

        self._logger.debug("Got API response: code=%s", api_response.code)

        if api_response.code != ApiResponseCode.SUCCESS.value:
            raise NanoKVMApiError(
                f"API returned error (Code: {api_response.code})",
                code=api_response.code,
                msg=api_response.msg,
                data=api_response.data,
            )

        if response_model is None:
            return None

        if api_response.data is None:
            raise NanoKVMInvalidResponseError("Successful API response is missing data")

        try:
            return response_model.model_validate(api_response.data)
        except ValidationError:
            raise NanoKVMInvalidResponseError("Invalid data in API response") from None

    async def upload_file(
        self,
        path: str,
        file_path: str | PathLike[str],
        response_model: type[T] | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Upload a file using the NanoKVM multipart API."""
        client = self._client
        upload_path = Path(file_path)
        form = aiohttp.FormData()

        with upload_path.open("rb") as file_obj:
            form.add_field("file", file_obj, filename=upload_path.name)
            return await client._api_request_form(
                hdrs.METH_POST,
                path,
                response_model=response_model,
                data=form,
                **kwargs,
            )

    async def do_authenticate(
        self, username: str, password_to_send: str, *, generation: int
    ) -> None:
        """Perform a single authentication attempt with the given password."""
        from .client import (
            _SESSION_COOKIE_NAME,
            NanoKVMApiError,
            NanoKVMAuthenticationFailure,
            NanoKVMInvalidResponseError,
        )

        client = self._client
        try:
            # NanoKVM 2.5.1 moved the session token from the JSON payload to a
            # HttpOnly Set-Cookie header. Read the envelope and response cookie
            # while the response context is still open; the cookie jar is not a
            # reliable source because callers may provide a DummyCookieJar or a
            # session shared with other services.
            async with client._request(
                hdrs.METH_POST,
                "/auth/login",
                authenticate=False,
                expected_generation=generation,
                json=LoginReq(username=username, password=password_to_send).model_dump(
                    by_alias=True,
                    exclude_none=True,
                ),
            ) as response:
                raw_response = await client._read_json_response(response)

                response_cookie = response.cookies.get(_SESSION_COOKIE_NAME)
                cookie_token = (
                    response_cookie.value.strip() if response_cookie is not None else ""
                )

            client._check_session_generation(generation)
            client._validate_api_response(raw_response)

            token = cookie_token
            if not token and raw_response.get("data") is not None:
                try:
                    token = LoginRsp.model_validate(raw_response["data"]).token.strip()
                except ValidationError:
                    raise NanoKVMInvalidResponseError(
                        "Invalid authentication response data"
                    ) from None

            if not token:
                raise NanoKVMInvalidResponseError(
                    "Authentication response missing token."
                )

            client._token = token
        except NanoKVMApiError as err:
            if err.code == ApiResponseCode.INVALID_USERNAME_OR_PASSWORD.value:
                raise NanoKVMAuthenticationFailure(
                    "Invalid username or password"
                ) from err
            raise

    async def authenticate(self, username: str, password: str) -> None:
        """Authenticate and store the session token."""
        from .client import NanoKVMAuthenticationFailure, obfuscate_password

        client = self._client
        # A failed identity switch must never leave the previous account usable.
        generation = await client._clear_local_session()
        self._logger.debug("Attempting authentication for user: %s", username)

        if client._use_password_obfuscation is True:
            self._logger.debug("Using password obfuscation (forced)")
            await client._do_authenticate(
                username, obfuscate_password(password), generation=generation
            )
        elif client._use_password_obfuscation is False:
            self._logger.debug("Using plain text password (forced)")
            await client._do_authenticate(username, password, generation=generation)
        else:
            self._logger.debug("Auto-detecting password mode")
            try:
                await client._do_authenticate(
                    username, obfuscate_password(password), generation=generation
                )
                client._use_password_obfuscation = True
                self._logger.info("Auto-detected obfuscated password mode")
            except NanoKVMAuthenticationFailure:
                self._logger.debug(
                    "Obfuscated authentication failed, trying plain text password"
                )
                await client._do_authenticate(username, password, generation=generation)
                client._use_password_obfuscation = False
                self._logger.info("Auto-detected plain text password mode")

        await client.detect_hardware()
        client._check_session_generation(generation)

    def check_session_generation(self, generation: int) -> None:
        """Reject an operation superseded by an authentication transition."""
        from .client import NanoKVMNotAuthenticatedError

        if generation != self._client._session_generation:
            raise NanoKVMNotAuthenticatedError(
                "Session changed while the operation was in progress"
            )

    async def logout(self) -> None:
        """Log out and clear the session token."""
        client = self._client
        generation = client._session_generation
        try:
            if client._token and client._token != "disabled":
                await client._api_request_json(hdrs.METH_POST, "/auth/logout")
        finally:
            await client._clear_local_session(expected_generation=generation)

    async def clear_local_session(
        self, *, expected_generation: int | None = None
    ) -> int:
        """Clear local authentication and transport state without closing HTTP."""
        client = self._client
        async with client._ws_lock:
            if (
                expected_generation is not None
                and expected_generation != client._session_generation
            ):
                return client._session_generation
            client._session_generation += 1
            generation = client._session_generation
            client._token = None
            client._mouse_buttons = 0
            client._clear_session_cookies()
            ws = client._ws
            client._ws = None
        # Detach state atomically, then close only the old transport. Network I/O
        # must not block a new login or close its replacement WebSocket.
        if ws is not None and not ws.closed:
            await ws.close()
        return generation

    def clear_session_cookies(self) -> None:
        """Remove only session cookies whose scope overlaps this device's API."""
        from .client import _SESSION_COOKIE_NAME

        client = self._client
        if client._session is None:
            return
        host = client.url.raw_host or ""
        base_path = client.url.path.rstrip("/")

        def is_device_session(cookie: Morsel[str]) -> bool:
            if cookie.key != _SESSION_COOKIE_NAME:
                return False
            domain = cookie["domain"].lstrip(".")
            if domain and host != domain and not host.endswith(f".{domain}"):
                return False
            path = cookie["path"].rstrip("/")
            return (
                base_path == path
                or base_path.startswith(f"{path}/")
                or path.startswith(f"{base_path}/")
            )

        client._session.cookie_jar.clear(is_device_session)

    async def uses_current_password_contract(self) -> bool:
        """Determine whether this device uses the 2.5.1 password contract."""
        from .client import (
            _CURRENT_PASSWORD_MIN_NON_PRO_VERSION,
            NanoKVMError,
            NanoKVMNotAuthenticatedError,
            _parse_version,
        )

        client = self._client
        if client._hw_version is None and client._token is None:
            raise NanoKVMNotAuthenticatedError("Client is not authenticated")
        if client._hw_version is None:
            await client.detect_hardware()

        assert client._hw_version is not None
        if client._is_hardware_family(HWFamily.PRO):
            return False

        if client._application_version is None:
            if client._token is None:
                raise NanoKVMNotAuthenticatedError("Client is not authenticated")
            await client.detect_versions()

        if client._application_version is None:
            raise NanoKVMError(
                "Application version must be identified before changing the password"
            )

        parsed_version = _parse_version(client._application_version)
        minimum_version = _parse_version(_CURRENT_PASSWORD_MIN_NON_PRO_VERSION)
        if parsed_version is None or minimum_version is None:
            raise NanoKVMError(
                "Application version must be identified before changing the password"
            )

        return parsed_version >= minimum_version

    async def change_password(
        self,
        username: str,
        new_password: str,
        *,
        current_password: str | None = None,
    ) -> None:
        """Change the KVM password for the authenticated account."""
        from .client import obfuscate_password

        client = self._client
        generation = client._session_generation
        if await client._uses_current_password_contract():
            if not current_password or not current_password.strip():
                raise ValueError(
                    "current_password is required for NanoKVM 2.5.1 and newer"
                )

            account = await client.get_account()
            client._check_session_generation(generation)
            if account.username != username:
                raise ValueError(
                    "username must match the authenticated account on NanoKVM 2.5.1"
                )

            await client._api_request_json(
                hdrs.METH_POST,
                "/auth/password",
                expected_generation=generation,
                data=ChangePasswordV251Req(
                    current_password=obfuscate_password(current_password),
                    password=obfuscate_password(new_password),
                ),
            )
            await client._clear_local_session(expected_generation=generation)
            return

        if client._use_password_obfuscation is None:
            raise ValueError(
                "Password mode is unknown. Authenticate first or set "
                "use_password_obfuscation explicitly before changing the password."
            )

        password_to_send = (
            obfuscate_password(new_password)
            if client._use_password_obfuscation
            else new_password
        )

        await client._api_request_json(
            hdrs.METH_POST,
            "/auth/password",
            expected_generation=generation,
            data=ChangePasswordReq(
                username=username,
                password=password_to_send,
            ),
        )
