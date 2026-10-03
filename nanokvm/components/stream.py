"""Internal MJPEG streaming and image decoding for the NanoKVM session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import io
import logging

import aiohttp
from aiohttp import BodyPartReader, MultipartReader, hdrs
from PIL import Image

from .session import Controller

_LOGGER = logging.getLogger(__name__)


class StreamController(Controller):
    """Own MJPEG frame transport and JPEG decoding."""

    def _parse_jpeg_from_bytes(self, data: bytes) -> Image.Image:
        """Parse a JPEG image from bytes."""
        with Image.open(io.BytesIO(data), formats=["JPEG"]) as image:
            image.load()
            return image.copy()

    async def mjpeg_stream(self) -> AsyncGenerator[Image.Image, None]:
        """Stream decoded MJPEG frames."""
        session = self._session
        generation = session._session_generation
        async with session.request(
            hdrs.METH_GET,
            "/stream/mjpeg",
            timeout=aiohttp.ClientTimeout(total=None, connect=session._request_timeout),
        ) as response:
            reader = MultipartReader.from_response(response)
            loop = asyncio.get_running_loop()

            async for part in reader:
                assert isinstance(part, BodyPartReader)
                data = await part.read()
                if not data:
                    _LOGGER.debug("Received empty MJPEG part, ending stream.")
                    break

                # Process image in executor to avoid blocking async loop.
                image = await loop.run_in_executor(
                    None, self._parse_jpeg_from_bytes, data
                )
                session.check_session_generation(generation)
                yield image
