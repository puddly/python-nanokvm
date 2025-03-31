"""Firmware update client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import io
import logging
from typing import Any

from aiohttp import ClientSession, MultipartReader
from PIL import Image
import yarl

from .utils import obfuscate_password

_LOGGER = logging.getLogger(__name__)

PASTE_CHAR_MAP = set(
    "\t\n !\"#$%&'()*+,-./0123456789"
    ":;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
)


class NanoKVMNotAuthenticated(Exception):
    pass


class NanoKVMApiError(Exception):
    pass


class NanoKVMClient:
    def __init__(self, url: str, session: ClientSession) -> None:
        self.url = yarl.URL(url)
        self.session = session
        self.token = None

    async def _api(
        self,
        method: str,
        endpoint: str,
        data: Any | None = None,
        authenticate: bool = True,
    ) -> Any:
        if authenticate and self.token is None:
            raise NanoKVMNotAuthenticated()

        _LOGGER.debug("Sending API request %s %s: %s", method, endpoint, data)

        async with self.session.request(
            method=method,
            url=self.url / endpoint,
            json=data,
            raise_for_status=True,
            cookies={"nano-kvm-token": self.token} if authenticate else None,
        ) as rsp:
            api_rsp = await rsp.json()

        _LOGGER.debug("Got API response: %s", api_rsp)

        if api_rsp["code"] != 0:
            raise NanoKVMApiError(f"API error: {api_rsp}")

        return api_rsp["data"]

    async def authenticate(self, username: str, password: str) -> None:
        rsp = await self._api(
            "POST",
            "auth/login",
            data={
                "username": username,
                "password": obfuscate_password(password),
            },
            authenticate=False,
        )
        _LOGGER.debug("Authenticated: %s", rsp)

        self.token = rsp["token"]

    async def get_device_info(self) -> Any:
        return await self._api("GET", "vm/info")

    async def get_hardware_info(self) -> Any:
        return await self._api("GET", "vm/hardware")

    async def get_gpio_state(self) -> Any:
        return await self._api("GET", "vm/gpio")

    async def send_wake_on_lan(self, mac: str) -> Any:
        return await self._api("POST", "network/wol", data={"mac": mac})

    async def mjpeg_stream(self) -> AsyncIterator[Image]:
        if self.token is None:
            raise NanoKVMNotAuthenticated()

        async with self.session.get(
            url=self.url / "stream/mjpeg",
            raise_for_status=True,
            cookies={"nano-kvm-token": self.token},
        ) as rsp:
            reader = MultipartReader.from_response(rsp)
            loop = asyncio.get_running_loop()

            while True:
                part = await reader.next()
                data = await part.read()

                image = await loop.run_in_executor(
                    None,
                    lambda data=data: Image.open(io.BytesIO(data), formats=["JPEG"]),
                )

                yield image

    async def send_keys(self, text: str) -> Any:
        invalid_chars = set(text) - PASTE_CHAR_MAP
        if invalid_chars:
            raise ValueError(f"Invalid characters in text: {invalid_chars}")

        return await self._api("POST", "hid/paste", data={"content": text})
