"""Internal MJPEG streaming and image decoding for the NanoKVM session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import contextlib
import io
import logging
from typing import Any

import aiohttp
from aiohttp import BodyPartReader, ClientWebSocketResponse, MultipartReader, hdrs
from PIL import Image

from ..exceptions import NanoKVMInvalidResponseError
from .session import Controller

_LOGGER = logging.getLogger(__name__)

_FRAME_TIMEOUT_SECONDS = 10.0


class StreamController(Controller):
    """Own MJPEG frame transport and JPEG decoding."""

    def _parse_jpeg_from_bytes(self, data: bytes) -> Image.Image:
        """Parse a JPEG image from bytes."""
        with Image.open(io.BytesIO(data), formats=["JPEG"]) as image:
            image.load()
            return image.copy()

    async def mjpeg_stream_bytes(self) -> AsyncGenerator[bytes, None]:
        """Stream raw JPEG frames, undecoded."""
        session = self._session
        generation = session._session_generation
        async with session.request(
            hdrs.METH_GET,
            "/stream/mjpeg",
            timeout=aiohttp.ClientTimeout(total=None, connect=session._request_timeout),
        ) as response:
            reader = MultipartReader.from_response(response)

            async for part in reader:
                assert isinstance(part, BodyPartReader)
                data = await part.read()
                if not data:
                    _LOGGER.debug("Received empty MJPEG part, ending stream.")
                    break

                session.check_session_generation(generation)
                yield data

    async def mjpeg_stream(self) -> AsyncGenerator[Image.Image, None]:
        """Stream decoded MJPEG frames."""
        session = self._session
        generation = session._session_generation
        loop = asyncio.get_running_loop()
        async with contextlib.aclosing(self.mjpeg_stream_bytes()) as frames:
            async for data in frames:
                # Process image in executor to avoid blocking async loop.
                image = await loop.run_in_executor(
                    None, self._parse_jpeg_from_bytes, data
                )
                session.check_session_generation(generation)
                yield image

    async def get_frame_bytes(
        self, *, timeout: float = _FRAME_TIMEOUT_SECONDS
    ) -> bytes:
        """Get one raw JPEG frame from the MJPEG stream, undecoded.

        ``timeout`` bounds the whole call, connecting included, so a device
        without a video signal raises ``TimeoutError`` instead of waiting
        forever. The stream is closed once the frame has arrived.
        """
        try:
            async with asyncio.timeout(timeout) as deadline:
                async with contextlib.aclosing(self.mjpeg_stream_bytes()) as frames:
                    async for data in frames:
                        return data
        except TimeoutError:
            if not deadline.expired():
                raise
            raise TimeoutError(
                f"No video frame arrived within {timeout:g} seconds"
            ) from None
        raise NanoKVMInvalidResponseError("The MJPEG stream ended without a frame")

    async def get_frame(
        self, *, timeout: float = _FRAME_TIMEOUT_SECONDS
    ) -> Image.Image:
        """Get one decoded frame from the MJPEG stream.

        ``timeout`` bounds the wait for the frame, as in ``get_frame_bytes``.
        """
        data = await self.get_frame_bytes(timeout=timeout)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._parse_jpeg_from_bytes, data)

    async def open_ws(self, path: str, **kwargs: Any) -> ClientWebSocketResponse:
        """Open a new, unshared WebSocket on ``path``, relative to the API base.

        For the stream signaling endpoints, such as ``stream/h264/webrtc``.
        ``kwargs`` go to ``ws_connect``, except ``headers`` and ``ssl``. The
        caller closes the WebSocket, before the client exits.
        """
        return await self._session.open_ws(path, **kwargs)
