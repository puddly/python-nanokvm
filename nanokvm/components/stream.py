"""Internal MJPEG streaming and image decoding for the NanoKVM client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import io
import logging
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import BodyPartReader, MultipartReader, hdrs
from PIL import Image

if TYPE_CHECKING:
    from ..client import NanoKVMClient


class StreamController:
    """Own MJPEG frame transport and JPEG decoding."""

    def __init__(self, client: NanoKVMClient, logger: logging.Logger) -> None:
        self._client = client
        self._logger = logger

    def parse_jpeg_from_bytes(self, data: bytes) -> Image.Image:
        """Parse a JPEG image from bytes."""
        with Image.open(io.BytesIO(data), formats=["JPEG"]) as image:
            image.load()
            return image.copy()

    async def mjpeg_stream(self) -> AsyncGenerator[Image.Image, None]:
        """Stream decoded MJPEG frames."""
        client = self._client
        async with client._request(
            hdrs.METH_GET,
            "/stream/mjpeg",
            timeout=aiohttp.ClientTimeout(total=None, connect=client._request_timeout),
        ) as response:
            reader = MultipartReader.from_response(response)
            loop = asyncio.get_running_loop()

            async for part in reader:
                assert isinstance(part, BodyPartReader)
                data = await part.read()
                if not data:
                    self._logger.debug("Received empty MJPEG part, ending stream.")
                    break

                # Process image in executor to avoid blocking async loop.
                image = await loop.run_in_executor(
                    None, client._parse_jpeg_from_bytes, data
                )
                yield image
