"""Tests for MJPEG streaming."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
import io
import time

from aiohttp import web
from aioresponses import aioresponses
from PIL import Image
import pytest

from nanokvm.client import NanoKVMClient, NanoKVMInvalidResponseError

from .common import BASE_URL

_MJPEG_HEADERS = {"Content-Type": "multipart/x-mixed-replace; boundary=frame"}


def _jpeg(size: tuple[int, int] = (2, 2)) -> bytes:
    image_buffer = io.BytesIO()
    Image.new("RGB", size).save(image_buffer, format="JPEG")
    return image_buffer.getvalue()


def _part(data: bytes) -> bytes:
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        + f"Content-Length: {len(data)}\r\n\r\n".encode()
        + data
        + b"\r\n"
    )


@asynccontextmanager
async def _mjpeg_server(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> AsyncGenerator[str, None]:
    application = web.Application()
    application.router.add_get("/api/stream/mjpeg", handler)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield f"http://127.0.0.1:{runner.addresses[0][1]}/api/"
    finally:
        await runner.cleanup()


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
    image_data = _jpeg()

    async def stream_handler(
        request: web.Request,
    ) -> web.StreamResponse:
        response = web.StreamResponse(headers=_MJPEG_HEADERS)
        await response.prepare(request)
        for _ in range(2):
            await response.write(_part(image_data))
            await asyncio.sleep(0.55)
        await response.write(b"--frame--\r\n")
        await response.write_eof()
        return response

    frames = []
    async with (
        _mjpeg_server(stream_handler) as url,
        NanoKVMClient(url, token="test-token", request_timeout=1) as client,
    ):
        async with asyncio.timeout(3):
            async for frame in client.mjpeg_stream():
                frames.append(frame)

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


async def test_get_frame_bytes_returns_first_frame_and_closes_stream() -> None:
    """One frame is returned and the device stops streaming right after it."""
    disconnected = asyncio.Event()

    async def endless_handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers=_MJPEG_HEADERS)
        await response.prepare(request)
        try:
            for index in range(1000):
                await response.write(_part(b"\xff\xd8frame-%d" % index))
                await asyncio.sleep(0.01)
        except (ConnectionError, asyncio.CancelledError):
            disconnected.set()
            raise
        return response

    async with (
        _mjpeg_server(endless_handler) as url,
        NanoKVMClient(url, token="test-token") as client,
    ):
        frame = await client.get_frame_bytes()
        async with asyncio.timeout(2):
            await disconnected.wait()

    assert frame == b"\xff\xd8frame-0"


async def test_get_frame_decodes_the_frame(mocked: aioresponses) -> None:
    """The decoded variant returns a loaded image."""
    mocked.get(
        f"{BASE_URL}stream/mjpeg",
        headers=_MJPEG_HEADERS,
        body=_part(_jpeg((4, 3))) + b"--frame--\r\n",
    )
    async with NanoKVMClient(BASE_URL, token="test-token") as client:
        image = await client.get_frame()

    assert image.size == (4, 3)
    assert getattr(image, "fp", None) is None


@pytest.mark.parametrize("phase", ["headers", "first frame"])
async def test_get_frame_bytes_times_out_without_a_frame(phase: str) -> None:
    """No frame within the timeout raises instead of waiting forever."""
    release = asyncio.Event()

    async def silent_handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers=_MJPEG_HEADERS)
        if phase == "first frame":
            await response.prepare(request)
        await release.wait()
        return response

    async with (
        _mjpeg_server(silent_handler) as url,
        NanoKVMClient(url, token="test-token", request_timeout=30) as client,
    ):
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="within 0.2 seconds"):
            await client.get_frame_bytes(timeout=0.2)
        elapsed = time.monotonic() - started
        release.set()

    assert elapsed < 2


async def test_get_frame_bytes_keeps_other_timeouts(
    client: NanoKVMClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport timeout before the deadline is not reported as no frame."""

    async def failing_stream() -> AsyncGenerator[bytes, None]:
        raise TimeoutError("connect timed out")
        yield b""

    monkeypatch.setattr(client, "mjpeg_stream_bytes", failing_stream)

    with pytest.raises(TimeoutError, match="connect timed out"):
        await client.get_frame_bytes(timeout=5)


async def test_get_frame_bytes_rejects_a_stream_without_frames(
    mocked: aioresponses,
) -> None:
    """A stream that ends before any frame is an invalid response."""
    mocked.get(f"{BASE_URL}stream/mjpeg", headers=_MJPEG_HEADERS, body=b"--frame--\r\n")
    async with NanoKVMClient(BASE_URL, token="test-token") as client:
        with pytest.raises(NanoKVMInvalidResponseError, match="without a frame"):
            await client.get_frame_bytes()
