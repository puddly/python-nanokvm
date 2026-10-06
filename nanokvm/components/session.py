"""Internal HTTP, authentication, and session handling for NanoKVM."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import contextlib
from http.cookies import Morsel, SimpleCookie
import json
import logging
from os import PathLike
from pathlib import Path
import ssl
from typing import Any, Literal, TypeVar, overload

from aiohttp import (
    ClientResponse,
    ClientResponseError,
    ClientSession,
    ClientTimeout,
    ClientWebSocketResponse,
    DummyCookieJar,
    Fingerprint,
    FormData,
    hdrs,
)
from pydantic import BaseModel, ValidationError
import yarl

from ..compatibility import _parse_version, _version_at_least
from ..exceptions import (
    NanoKVMApiError,
    NanoKVMAuthenticationFailure,
    NanoKVMError,
    NanoKVMInvalidResponseError,
    NanoKVMNotAuthenticatedError,
    NanoKVMPermissionError,
)
from ..models.common import (
    ApiResponse,
    ApiResponseCode,
    ChangePasswordReq,
    ChangePasswordV251Req,
    GetAccountRsp,
    GetHardwareRsp,
    GetInfoRsp,
    HWFamily,
    HWVersion,
    IsPasswordUpdatedRsp,
    LoginReq,
    LoginRsp,
)
from ..utils import obfuscate_password

T = TypeVar("T", bound=BaseModel)

_LOGGER = logging.getLogger(__name__)

_SESSION_COOKIE_NAME = "nano-kvm-token"
_NOT_STARTED = "NanoKVMClient is not started; use 'async with NanoKVMClient(...)'"
_CURRENT_PASSWORD_MIN_NON_PRO_VERSION = "2.5.1"


class SessionController:
    """Own authentication, shared state, HTTP and WebSocket resources."""

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
        """Initialize the shared session, transport and device state."""
        self.url = yarl.URL(url)
        self._http_session: ClientSession | None = session
        self._external_session_provided = session is not None
        self._token = token
        self._session_generation = 0
        self._request_timeout = request_timeout
        self._image_transfer_lock = asyncio.Lock()
        self._ws: ClientWebSocketResponse | None = None
        self._ws_session: ClientSession | None = None
        self._ws_lock = asyncio.Lock()
        self._mouse_buttons = 0
        self._mouse_mode: Literal["relative", "absolute"] = "relative"
        self._mouse_abs_position: tuple[int, int] = (0, 0)
        self._verify_ssl = verify_ssl
        self._ssl_ca_cert = ssl_ca_cert
        self._ssl_fingerprint = ssl_fingerprint
        self._use_password_obfuscation = use_password_obfuscation
        self._ssl_config: ssl.SSLContext | Fingerprint | bool | None = None
        self._hw_version: HWVersion | None = None
        self._application_version: str | None = None
        self._image_version: str | None = None

    def create_ssl_context(self) -> ssl.SSLContext | Fingerprint | bool:
        """Create and configure SSL context from the client's settings."""
        if self._ssl_fingerprint:
            _LOGGER.debug("Using certificate fingerprint pinning")
            return Fingerprint(bytes.fromhex(self._ssl_fingerprint.replace(":", "")))

        if not self._verify_ssl:
            _LOGGER.warning(
                "SSL verification is disabled. This is insecure and should only be "
                "used for testing with self-signed certificates."
            )
            return False

        if not self._ssl_ca_cert:
            return True

        ssl_ctx = ssl.create_default_context(cafile=self._ssl_ca_cert)
        _LOGGER.debug("Using custom CA certificate: %s", self._ssl_ca_cert)
        return ssl_ctx

    async def enter(self) -> None:
        """Initialize the client's SSL configuration and HTTP session."""
        self._ssl_config = await asyncio.to_thread(self.create_ssl_context)
        if self._http_session is None and not self._external_session_provided:
            self._http_session = ClientSession()

    async def exit(self) -> None:
        """Close the client's WebSocket and owned HTTP resources."""
        await self.close_ws()

        if self._ws_session is not None:
            await self._ws_session.close()
            self._ws_session = None

        if self._http_session is not None and not self._external_session_provided:
            await self._http_session.close()
            self._http_session = None

    @contextlib.asynccontextmanager
    async def request(
        self,
        method: str,
        path: str,
        *,
        authenticate: bool = True,
        timeout: ClientTimeout | None = None,
        expected_generation: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ClientResponse, None]:
        """Make an authenticated or unauthenticated API request."""

        if expected_generation is not None:
            self.check_session_generation(expected_generation)
        generation = self._session_generation
        cookies = {}
        if authenticate:
            if not self._token:
                raise NanoKVMNotAuthenticatedError("Client is not authenticated")
            cookies[_SESSION_COOKIE_NAME] = self._token

        if self._http_session is None or self._ssl_config is None:
            raise RuntimeError(_NOT_STARTED)

        self.clear_session_cookies()
        request_headers = {
            hdrs.ACCEPT: "application/json",
            **kwargs.pop("headers", {}),
        }

        async with self._http_session.request(
            method,
            self.url / path.lstrip("/"),
            headers=request_headers,
            cookies=cookies,
            timeout=timeout or ClientTimeout(total=self._request_timeout),
            raise_for_status=False,
            ssl=self._ssl_config,
            **kwargs,
        ) as response:
            # The explicit token owns the session. Do not leave response cookies
            # available to another client sharing this HTTP session.
            self.clear_session_cookies()
            if authenticate:
                self.check_session_generation(generation)
            if authenticate and response.status == 401:
                await self.clear_local_session(expected_generation=generation)
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
            if authenticate:
                self.check_session_generation(generation)

    async def read_json_response(self, response: ClientResponse) -> Any:
        """Read a JSON response and normalize decoding failures."""
        try:
            return await response.json(content_type=None)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            raise NanoKVMInvalidResponseError(
                "Invalid JSON response received"
            ) from None

    @overload
    async def api_request_json(
        self,
        method: str,
        path: str,
        response_model: type[T],
        data: BaseModel | FormData | None = None,
        **kwargs: Any,
    ) -> T: ...

    @overload
    async def api_request_json(
        self,
        method: str,
        path: str,
        response_model: None = None,
        data: BaseModel | FormData | None = None,
        **kwargs: Any,
    ) -> None: ...

    async def api_request_json(
        self,
        method: str,
        path: str,
        response_model: type[T] | None = None,
        data: BaseModel | FormData | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Make an API request and parse its JSON response.

        A model is sent as a JSON body and ``FormData`` as multipart.
        """
        _LOGGER.debug("Making API request: %s %s", method, path)

        if isinstance(data, BaseModel):
            kwargs["json"] = data.model_dump(by_alias=True, exclude_none=True)
        elif data is not None:
            kwargs["data"] = data

        async with self.request(method, path, **kwargs) as response:
            raw_response = await self.read_json_response(response)

        return self.validate_api_response(raw_response, response_model)

    def validate_api_response(
        self,
        raw_response: Any,
        response_model: type[T] | None = None,
    ) -> T | None:
        """Validate the shared NanoKVM response envelope."""

        try:
            api_response = ApiResponse[Any].model_validate(raw_response)
        except ValidationError:
            raise NanoKVMInvalidResponseError("Invalid API response envelope") from None

        _LOGGER.debug("Got API response: code=%s", api_response.code)

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

    @overload
    async def upload_file(
        self,
        path: str,
        file_path: str | PathLike[str],
        response_model: type[T],
        **kwargs: Any,
    ) -> T: ...

    @overload
    async def upload_file(
        self,
        path: str,
        file_path: str | PathLike[str],
        response_model: None = None,
        **kwargs: Any,
    ) -> None: ...

    async def upload_file(
        self,
        path: str,
        file_path: str | PathLike[str],
        response_model: type[T] | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Upload a file using the NanoKVM multipart API."""
        upload_path = Path(file_path)
        form = FormData()

        with upload_path.open("rb") as file_obj:
            form.add_field("file", file_obj, filename=upload_path.name)
            return await self.api_request_json(
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
        try:
            # NanoKVM 2.5.1 moved the session token from the JSON payload to a
            # HttpOnly Set-Cookie header. Read the envelope and response cookie
            # while the response context is still open; the cookie jar is not a
            # reliable source because callers may provide a DummyCookieJar or a
            # session shared with other services.
            async with self.request(
                hdrs.METH_POST,
                "/auth/login",
                authenticate=False,
                expected_generation=generation,
                json=LoginReq(username=username, password=password_to_send).model_dump(
                    by_alias=True,
                    exclude_none=True,
                ),
            ) as response:
                raw_response = await self.read_json_response(response)

                response_cookie = response.cookies.get(_SESSION_COOKIE_NAME)
                cookie_token = (
                    response_cookie.value.strip() if response_cookie is not None else ""
                )

            self.check_session_generation(generation)
            self.validate_api_response(raw_response)

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

            self._token = token
        except NanoKVMApiError as err:
            if err.code == ApiResponseCode.INVALID_USERNAME_OR_PASSWORD.value:
                raise NanoKVMAuthenticationFailure(
                    "Invalid username or password"
                ) from err
            raise

    async def authenticate(self, username: str, password: str) -> int:
        """Authenticate and store the session token."""
        # A failed identity switch must never leave the previous account usable.
        generation = await self.clear_local_session()
        _LOGGER.debug("Attempting authentication for user: %s", username)

        if self._use_password_obfuscation is True:
            _LOGGER.debug("Using password obfuscation (forced)")
            await self.do_authenticate(
                username, obfuscate_password(password), generation=generation
            )
        elif self._use_password_obfuscation is False:
            _LOGGER.debug("Using plain text password (forced)")
            await self.do_authenticate(username, password, generation=generation)
        else:
            _LOGGER.debug("Auto-detecting password mode")
            try:
                await self.do_authenticate(
                    username, obfuscate_password(password), generation=generation
                )
                self._use_password_obfuscation = True
                _LOGGER.info("Auto-detected obfuscated password mode")
            except NanoKVMAuthenticationFailure:
                _LOGGER.debug(
                    "Obfuscated authentication failed, trying plain text password"
                )
                await self.do_authenticate(username, password, generation=generation)
                self._use_password_obfuscation = False
                _LOGGER.info("Auto-detected plain text password mode")

        self.check_session_generation(generation)
        return generation

    def check_session_generation(self, generation: int) -> None:
        """Reject an operation superseded by an authentication transition."""

        if generation != self._session_generation:
            raise NanoKVMNotAuthenticatedError(
                "Session changed while the operation was in progress"
            )

    async def logout(self) -> None:
        """Log out and clear the session token."""
        generation = self._session_generation
        try:
            if self._token and self._token != "disabled":
                try:
                    await self.api_request_json(hdrs.METH_POST, "/auth/logout")
                except ClientResponseError as err:
                    if err.status != 404:
                        raise
        finally:
            await self.clear_local_session(expected_generation=generation)

    async def clear_local_session(
        self, *, expected_generation: int | None = None
    ) -> int:
        """Clear local authentication and transport state without closing HTTP."""
        async with self._ws_lock:
            if (
                expected_generation is not None
                and expected_generation != self._session_generation
            ):
                return self._session_generation
            self._session_generation += 1
            generation = self._session_generation
            self._token = None
            self.forget_versions()
            self._mouse_buttons = 0
            self.clear_session_cookies()
            ws = self._ws
            self._ws = None
        # Detach state atomically, then close only the old transport. Network I/O
        # must not block a new login or close its replacement WebSocket.
        if ws is not None and not ws.closed:
            await ws.close()
        return generation

    def clear_session_cookies(self) -> None:
        """Remove only session cookies whose scope overlaps this device's API."""
        if self._http_session is None:
            return
        host = self.url.raw_host or ""
        base_path = self.url.path.rstrip("/")

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

        self._http_session.cookie_jar.clear(is_device_session)

    async def uses_current_password_contract(self) -> bool:
        """Determine whether this device uses the 2.5.1 password contract."""
        if self._hw_version is None and self._token is None:
            raise NanoKVMNotAuthenticatedError("Client is not authenticated")
        if self._hw_version is None:
            await self.detect_hardware()

        assert self._hw_version is not None
        if self.is_hardware_family(HWFamily.PRO):
            return False

        if self._application_version is None:
            if self._token is None:
                raise NanoKVMNotAuthenticatedError("Client is not authenticated")
            await self.detect_versions()

        if self._application_version is None:
            raise NanoKVMError(
                "Application version must be identified before changing the password"
            )

        parsed_version = _parse_version(self._application_version)
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
        generation = self._session_generation
        if await self.uses_current_password_contract():
            if not current_password or not current_password.strip():
                raise ValueError(
                    "current_password is required for NanoKVM 2.5.1 and newer"
                )

            account = await self.get_account()
            self.check_session_generation(generation)
            if account.username != username:
                raise ValueError(
                    "username must match the authenticated account on NanoKVM 2.5.1"
                )

            await self.api_request_json(
                hdrs.METH_POST,
                "/auth/password",
                expected_generation=generation,
                data=ChangePasswordV251Req.model_validate(
                    {
                        "currentPassword": obfuscate_password(current_password),
                        "password": obfuscate_password(new_password),
                    }
                ),
            )
            await self.clear_local_session(expected_generation=generation)
            return

        if self._use_password_obfuscation is None:
            raise ValueError(
                "Password mode is unknown. Authenticate first or set "
                "use_password_obfuscation explicitly before changing the password."
            )

        password_to_send = (
            obfuscate_password(new_password)
            if self._use_password_obfuscation
            else new_password
        )

        await self.api_request_json(
            hdrs.METH_POST,
            "/auth/password",
            expected_generation=generation,
            data=ChangePasswordReq(
                username=username,
                password=password_to_send,
            ),
        )

    async def is_password_updated(self) -> IsPasswordUpdatedRsp:
        """Check if the default password has been changed."""
        return await self.api_request_json(
            hdrs.METH_GET,
            "/auth/password",
            response_model=IsPasswordUpdatedRsp,
        )

    async def get_account(self) -> GetAccountRsp:
        """Get the configured username."""
        return await self.api_request_json(
            hdrs.METH_GET,
            "/auth/account",
            response_model=GetAccountRsp,
        )

    def is_hardware_family(self, family: HWFamily) -> bool:
        """Return whether the detected hardware belongs to ``family``."""
        return self._hw_version is not None and self._hw_version.family is family

    async def application_version_at_least(
        self, *, non_pro: str | None = None, pro: str | None = None
    ) -> bool:
        """Return whether the device meets its family's minimum application version.

        Unknown hardware or versions are detected when a token is available and
        are otherwise treated as supported.
        """
        if self._hw_version is None:
            if self._token is None:
                return True
            await self.detect_hardware()

        minimum = pro if self.is_hardware_family(HWFamily.PRO) else non_pro
        if minimum is None:
            return True

        if self._application_version is None:
            if self._token is None:
                return True
            await self.detect_versions()

        return self._application_version is None or _version_at_least(
            self._application_version, minimum
        )

    def forget_versions(self) -> None:
        """Drop cached firmware versions so the next check detects them again."""
        self._application_version = None
        self._image_version = None

    async def detect_hardware(self) -> None:
        """Detect and store the hardware version."""
        hw = await self.get_hardware()
        self._hw_version = hw.version
        _LOGGER.info("Detected hardware: %s", hw.version)

    async def detect_versions(self) -> None:
        """Detect and store image and application versions."""
        info = await self.get_info()
        self._application_version = info.application
        self._image_version = info.image
        _LOGGER.info(
            "Detected versions: application=%s image=%s",
            info.application,
            info.image,
        )

    async def get_info(self) -> GetInfoRsp:
        """Get general device information."""
        return await self.api_request_json(
            hdrs.METH_GET,
            "/vm/info",
            response_model=GetInfoRsp,
        )

    async def get_hardware(self) -> GetHardwareRsp:
        """Get hardware version information."""
        return await self.api_request_json(
            hdrs.METH_GET,
            "/vm/hardware",
            response_model=GetHardwareRsp,
        )

    async def close_ws(self) -> None:
        """Close and forget the current WebSocket connection."""
        async with self._ws_lock:
            ws = self._ws
            self._ws = None
            if ws is not None and not ws.closed:
                await ws.close()

    async def invalidate_ws(self, ws: ClientWebSocketResponse) -> None:
        """Forget a failed WebSocket without closing a replacement connection."""
        async with self._ws_lock:
            if self._ws is ws:
                self._ws = None
            if not ws.closed:
                await ws.close()

    async def get_ws(self) -> ClientWebSocketResponse:
        """Get or create WebSocket connection for mouse events."""
        generation = self._session_generation
        try:
            async with self._ws_lock:
                if self._ws is not None and not self._ws.closed:
                    return self._ws

                if self._ws is not None:
                    await self._ws.close()
                    self._ws = None

                if not self._token:
                    raise NanoKVMNotAuthenticatedError("Client is not authenticated")

                generation = self._session_generation

                # WebSocket URL uses ws:// or wss:// scheme
                scheme = "ws" if self.url.scheme == "http" else "wss"
                ws_url = self.url.with_scheme(scheme) / "ws"

                if self._http_session is None or self._ssl_config is None:
                    raise RuntimeError(_NOT_STARTED)

                # ws_connect cannot override cookies per request. An isolated
                # jar prevents concurrent requests or async tracing callbacks
                # from replacing this handshake's identity. Share the external
                # connector without taking ownership of it.
                if self._http_session.closed:
                    raise RuntimeError("Session is closed")
                if self._ws_session is None or self._ws_session.closed:
                    self._ws_session = ClientSession(
                        connector=self._http_session.connector,
                        connector_owner=False,
                        cookie_jar=DummyCookieJar(),
                        auth=self._http_session.auth,
                        trust_env=self._http_session.trust_env,
                        trace_configs=self._http_session.trace_configs,
                        skip_auto_headers=self._http_session.skip_auto_headers,
                        timeout=ClientTimeout(total=self._request_timeout),
                    )

                headers = self._http_session.headers.copy()
                cookies = SimpleCookie()
                cookies.load(headers.get(hdrs.COOKIE, ""))
                jar_cookies = self._http_session.cookie_jar.filter_cookies(ws_url)
                cookies.load(
                    {name: cookie.value for name, cookie in jar_cookies.items()}
                )
                cookies[_SESSION_COOKIE_NAME] = self._token
                headers[hdrs.COOKIE] = cookies.output(header="", sep=";").strip()
                self.clear_session_cookies()
                self._ws = await self._ws_session.ws_connect(
                    str(ws_url),
                    headers=headers,
                    ssl=self._ssl_config,
                )
                self.clear_session_cookies()
                return self._ws
        except ClientResponseError as err:
            method = getattr(err.request_info, "method", hdrs.METH_GET)
            if err.status == 401:
                # _clear_local_session acquires _ws_lock, so do this after the
                # lock above has been released to avoid a self-deadlock.
                await self.clear_local_session(expected_generation=generation)
                raise NanoKVMNotAuthenticatedError(
                    "NanoKVM session is no longer authenticated"
                ) from err
            if err.status == 403:
                raise NanoKVMPermissionError(
                    status=err.status,
                    method=method,
                    path="/ws",
                ) from err
            raise


class Controller:
    """Base for endpoint controllers that share one session."""

    def __init__(self, session: SessionController) -> None:
        self._session = session
