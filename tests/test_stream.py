"""Tests for MJPEG streaming."""

import asyncio
import io

from aiohttp import web
from aioresponses import aioresponses
from PIL import Image
import pytest

from nanokvm.client import NanoKVMClient
from nanokvm.exceptions import NanoKVMInvalidResponseError, NanoKVMNotSupportedError
from nanokvm.models import HWVersion

from .common import BASE_URL, mark_detected


def test_parse_jpeg_returns_fully_loaded_image() -> None:
    """JPEG parsing returns an image detached from the source stream."""
    image_buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(1, 2, 3)).save(image_buffer, format="JPEG")
    client = NanoKVMClient(BASE_URL)

    image = client._parse_jpeg_from_bytes(image_buffer.getvalue())

    assert image.getpixel((0, 0))
    assert getattr(image, "fp", None) is None


async def test_mjpeg_stream_allows_frames_after_request_timeout() -> None:
    """An MJPEG stream remains open beyond the normal request timeout."""
    image_buffer = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image_buffer, format="JPEG")
    image_data = image_buffer.getvalue()

    async def stream_handler(
        request: web.Request,
    ) -> web.StreamResponse:
        response = web.StreamResponse(
            headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"}
        )
        await response.prepare(request)
        for _ in range(2):
            await response.write(
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(image_data)}\r\n\r\n".encode()
                + image_data
                + b"\r\n"
            )
            await asyncio.sleep(0.55)
        await response.write(b"--frame--\r\n")
        await response.write_eof()
        return response

    application = web.Application()
    application.router.add_get("/api/stream/mjpeg", stream_handler)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server_sockets = getattr(site._server, "sockets", None)
    assert server_sockets
    port = server_sockets[0].getsockname()[1]

    frames = []
    try:
        async with NanoKVMClient(
            f"http://127.0.0.1:{port}/api/", token="test-token", request_timeout=1
        ) as client:
            async with asyncio.timeout(3):
                async for frame in client.mjpeg_stream():
                    frames.append(frame)
    finally:
        await runner.cleanup()

    assert len(frames) == 2


async def test_mjpeg_stream_bytes_yields_raw_frames(mocked: aioresponses) -> None:
    """The byte stream hands out each JPEG part untouched."""
    mocked.get(
        f"{BASE_URL}stream/mjpeg",
        headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"},
        body=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8one\r\n"
        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8two\r\n--frame--\r\n",
    )
    async with NanoKVMClient(BASE_URL, token="test-token") as c:
        frames = [frame async for frame in c.mjpeg_stream_bytes()]

    assert frames == [b"\xff\xd8one", b"\xff\xd8two"]


_MJPEG_HEADERS = {"Content-Type": "multipart/x-mixed-replace; boundary=frame"}


async def test_get_snapshot_returns_first_frame(mocked: aioresponses) -> None:
    """A snapshot is the first raw JPEG part of the stream."""
    mocked.get(
        f"{BASE_URL}stream/mjpeg",
        headers=_MJPEG_HEADERS,
        body=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8one\r\n"
        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8two\r\n--frame--\r\n",
    )
    async with NanoKVMClient(BASE_URL, token="test-token") as c:
        mark_detected(c)
        assert await c.get_snapshot() == b"\xff\xd8one"


async def test_get_snapshot_without_frames_raises(mocked: aioresponses) -> None:
    """A stream that ends before any frame is an error, not None."""
    mocked.get(f"{BASE_URL}stream/mjpeg", headers=_MJPEG_HEADERS, body=b"--frame--\r\n")
    async with NanoKVMClient(BASE_URL, token="test-token") as c:
        mark_detected(c)
        with pytest.raises(NanoKVMInvalidResponseError, match="without a frame"):
            await c.get_snapshot()


async def test_get_snapshot_is_refused_on_the_pro(mocked: aioresponses) -> None:
    """The Pro never opens MJPEG for a snapshot, so WebRTC stays up."""
    async with NanoKVMClient(BASE_URL, token="test-token") as c:
        mark_detected(c, HWVersion.PRO)
        with pytest.raises(NanoKVMNotSupportedError, match="interrupt_webrtc"):
            await c.get_snapshot()
    assert not mocked.requests


async def test_get_snapshot_on_the_pro_when_interrupting_webrtc(
    mocked: aioresponses,
) -> None:
    """The Pro takes a snapshot once the caller accepts ending WebRTC."""
    mocked.get(
        f"{BASE_URL}stream/mjpeg",
        headers=_MJPEG_HEADERS,
        body=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8one\r\n--frame--\r\n",
    )
    async with NanoKVMClient(BASE_URL, token="test-token") as c:
        mark_detected(c, HWVersion.PRO)
        assert await c.get_snapshot(interrupt_webrtc=True) == b"\xff\xd8one"
