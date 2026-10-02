"""Contract tests for remote image downloads and local image uploads."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import io
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
import pytest

from nanokvm import models
from nanokvm.client import (
    NanoKVMClient,
    NanoKVMNotSupportedError,
    NanoKVMPermissionError,
)
from nanokvm.models import DownloadStatus, HWVersion, StatusImageRsp


def _status(status: DownloadStatus) -> StatusImageRsp:
    return StatusImageRsp(
        status=status,
        file="https://images.example.test/synthetic.iso",
        percentage="",
    )


@asynccontextmanager
async def _device_server() -> AsyncIterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {
        "calls": [],
        "accepted_uploads": [],
        "http_status": {},
        "upload_http_statuses": [],
        "pro_images": [],
        "after_pro_upload": None,
    }

    async def handler(request: web.Request) -> web.Response:
        call: dict[str, Any] = {
            "method": request.method,
            "path": request.path,
            "headers": dict(request.headers),
        }
        state["calls"].append(call)

        if request.content_type == "application/json":
            call["payload"] = await request.json()
            if request.path == "/api/storage/image/delete":
                state["pro_images"] = [
                    file
                    for file in state["pro_images"]
                    if file != call["payload"]["file"]
                ]
        elif request.content_type == "multipart/form-data":
            reader = await request.multipart()
            fields = []
            while field := await reader.next():
                assert isinstance(field, aiohttp.BodyPartReader)
                fields.append(
                    {
                        "name": field.name,
                        "filename": field.filename,
                        "value": await field.read(decode=False),
                    }
                )
            call["payload"] = fields
            file_values = [
                field["value"] for field in fields if field["name"] == "file"
            ]
            state["accepted_uploads"].append(
                b"".join(
                    bytes(value)
                    for value in file_values
                    if isinstance(value, (bytes, bytearray, memoryview))
                )
            )
            if request.path == "/api/storage/image/upload":
                for multipart_field in fields:
                    filename = multipart_field["filename"]
                    if multipart_field["name"] == "file" and isinstance(filename, str):
                        data_path = f"/data/{filename}"
                        if data_path not in state["pro_images"]:
                            state["pro_images"].append(data_path)
            after_pro_upload = state["after_pro_upload"]
            if request.path == "/api/storage/image/upload" and after_pro_upload:
                state["after_pro_upload"] = None
                after_pro_upload()

        data: dict[str, Any] | None
        if request.method == "GET" and request.path == "/api/storage/image":
            data = {"files": state["pro_images"]}
        elif request.path.endswith("/image/status"):
            data = {
                "status": "in_progress",
                "file": "https://images.example.test/synthetic.iso",
                "percentage": "50%",
            }
        elif request.path.endswith("/image") and request.method == "POST":
            data = {
                "status": "in_progress",
                "file": "https://images.example.test/synthetic.iso",
                "percentage": "",
            }
        else:
            data = None

        upload_http_statuses = state["upload_http_statuses"]
        if request.path == "/api/storage/image/upload" and upload_http_statuses:
            status = upload_http_statuses.pop(0)
        else:
            status = state["http_status"].get(request.path, 200)
        return web.json_response(
            {"code": 0, "msg": "success", "data": data}, status=status
        )

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = cast(Any, site._server)
    assert server is not None
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/api/", state
    finally:
        await runner.cleanup()


def _mark(client: NanoKVMClient, hardware: HWVersion, version: str) -> None:
    client._session._hw_version = hardware
    client._session._application_version = version


async def test_download_image_accepts_sha256_and_keeps_legacy_route() -> None:
    """Non-Pro checksum downloads use sha256sum while calls without it stay stable."""
    remote_url = "https://images.example.test/synthetic.iso"
    checksum = "a" * 64

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PCIE, "2.5.2")
        await client.download_image(remote_url, sha256=checksum)
        await client.download_image(remote_url)

    posts = [call for call in state["calls"] if call["method"] == "POST"]
    assert [call["path"] for call in posts] == [
        "/api/download/image",
        "/api/download/image",
    ]
    assert posts[0]["payload"] == {"file": remote_url, "sha256sum": checksum}
    assert posts[1]["payload"] == {"file": remote_url}


async def test_download_image_rejects_checksum_on_pro_before_endpoint_io() -> None:
    """Pro does not accept the non-Pro remote checksum contract."""
    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PRO, "1.2.15")
        with pytest.raises(NanoKVMNotSupportedError):
            await client.download_image(
                "https://images.example.test/synthetic.iso", sha256="a" * 64
            )

    assert state["calls"] == []


async def test_download_image_rejects_checksum_below_250_before_endpoint_io() -> None:
    """The optional checksum is only available from non-Pro 2.5.0."""
    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PCIE, "2.4.9")
        with pytest.raises(NanoKVMNotSupportedError):
            await client.download_image(
                "https://images.example.test/synthetic.iso", sha256="a" * 64
            )

    assert state["calls"] == []


@pytest.mark.parametrize("sha256", ["", "a" * 63, "g" * 64])
async def test_download_image_rejects_malformed_sha256_before_io(
    sha256: str,
) -> None:
    """A supplied download checksum must be a non-empty SHA-256 before I/O."""
    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PCIE, "2.5.2")
        with pytest.raises(ValueError, match="64 hexadecimal"):
            await client.download_image(
                "https://images.example.test/synthetic.iso", sha256=sha256
            )

    assert state["calls"] == []


async def test_cancel_image_download_uses_non_pro_250_route() -> None:
    """Only supported non-Pro firmware receives a download cancellation POST."""
    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PCIE, "2.5.0")
        await client.cancel_image_download()

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("POST", "/api/download/image/cancel")
    ]


@pytest.mark.parametrize(
    "terminal",
    [
        DownloadStatus.IDLE,
        DownloadStatus.SUCCESS,
        DownloadStatus.FAILED,
        DownloadStatus.CHECKSUM_FAILED,
    ],
)
async def test_image_download_watcher_stops_on_every_terminal_status(
    terminal: DownloadStatus,
) -> None:
    """The watcher yields the terminal result and performs no later poll."""
    async with NanoKVMClient(
        "http://127.0.0.1:1/api/", token="synthetic-token"
    ) as client:
        status_request = AsyncMock(return_value=_status(terminal))
        with patch.object(
            client._storage_controller, "get_image_download_status", status_request
        ):
            results = [
                result
                async for result in client.watch_image_download(poll_interval=0.001)
            ]

    assert [result.status for result in results] == [terminal]
    status_request.assert_awaited_once_with()


async def test_image_download_watcher_polls_until_success_then_stops() -> None:
    """In-progress results are yielded before the terminal success result."""
    async with NanoKVMClient(
        "http://127.0.0.1:1/api/", token="synthetic-token"
    ) as client:
        status_request = AsyncMock(
            side_effect=[
                _status(DownloadStatus.IN_PROGRESS),
                _status(DownloadStatus.SUCCESS),
            ]
        )
        with patch.object(
            client._storage_controller, "get_image_download_status", status_request
        ):
            results = [
                result
                async for result in client.watch_image_download(poll_interval=0.001)
            ]

    assert [result.status for result in results] == [
        DownloadStatus.IN_PROGRESS,
        DownloadStatus.SUCCESS,
    ]
    assert status_request.await_count == 2


async def test_image_download_watcher_rejects_nonpositive_interval_before_poll() -> (
    None
):
    """A zero or negative polling delay fails before requesting status."""
    async with NanoKVMClient(
        "http://127.0.0.1:1/api/", token="synthetic-token"
    ) as client:
        status_request = AsyncMock(return_value=_status(DownloadStatus.IDLE))
        with (
            patch.object(
                client._storage_controller, "get_image_download_status", status_request
            ),
            pytest.raises(ValueError, match="poll_interval"),
        ):
            async for _ in client.watch_image_download(poll_interval=0):
                pass

    status_request.assert_not_awaited()


async def test_non_pro_upload_streams_file_and_reports_sync_progress(
    tmp_path: Path,
) -> None:
    """A non-Pro multipart upload streams one file with bounded progress updates."""
    image = tmp_path / "synthetic_name-1.ISO"
    image.write_bytes(b"0123456")
    events: list[Any] = []
    accepted_at_final: list[bool] = []

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PCIE, "2.5.2")

        def progress(item: models.ImageTransferProgress) -> None:
            events.append(item)
            if len(events) > 1 and item.bytes_transferred == item.total_bytes:
                accepted_at_final.append(state["accepted_uploads"] == [b"0123456"])

        await client.upload_image(
            image,
            chunk_size=3,
            sha256="b" * 64,
            progress_callback=progress,
        )

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image"),
        ("POST", "/api/download/file"),
    ]
    request = state["calls"][1]
    assert request["headers"]["X-SHA256-Sum"] == "b" * 64
    assert request["payload"] == [
        {"name": "file", "filename": "synthetic_name-1.ISO", "value": b"0123456"}
    ]
    assert [event.bytes_transferred for event in events] == [0, 3, 6, 7]
    assert [event.percentage for event in events] == pytest.approx(
        [0, 300 / 7, 600 / 7, 100]
    )
    assert state["accepted_uploads"] == [b"0123456"]
    assert accepted_at_final == [True]
    assert all("0123456" not in str(event) for event in events)
    assert getattr(models, "ImageTransferProgress", None) is not None


async def test_pro_upload_sends_chunks_and_accepts_async_progress(
    tmp_path: Path,
) -> None:
    """Pro uploads preserve safe Unicode names and permit an /sdcard homonym."""
    image = tmp_path / "仮想 １２-test_01.ISO"
    image.write_bytes(b"abcdef")
    events: list[Any] = []

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["pro_images"] = [f"/sdcard/{image.name}"]
        _mark(client, HWVersion.PRO, "1.2.15")

        async def progress(item: models.ImageTransferProgress) -> None:
            events.append(item)

        await client.upload_image(image, chunk_size=2, progress_callback=progress)

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image"),
        ("POST", "/api/storage/image/upload"),
        ("POST", "/api/storage/image/upload"),
        ("POST", "/api/storage/image/upload"),
    ]
    for index, request in enumerate(state["calls"][1:]):
        fields = request["payload"]
        assert {field["name"]: field["value"] for field in fields[:3]} == {
            "chunkIndex": str(index).encode(),
            "chunkSize": b"2",
            "totalChunks": b"3",
        }
        assert fields[3] == {
            "name": "file",
            "filename": image.name,
            "value": b"abcdef"[index * 2 : index * 2 + 2],
        }
    assert [event.bytes_transferred for event in events] == [0, 2, 4, 6]
    assert events[-1].percentage == 100


async def test_pro_upload_fills_chunks_after_partial_raw_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short raw read does not look like an image truncated during upload."""
    image = tmp_path / "partial-reads.iso"
    image_bytes = b"abcdef"
    image.write_bytes(image_bytes)
    original_open = Path.open

    class PartialReadStream(io.RawIOBase):
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._position = 0

        def readable(self) -> bool:
            return True

        def readinto(self, buffer: Any) -> int:
            if self._position >= len(self._data):
                return 0
            size = min(1, len(buffer), len(self._data) - self._position)
            buffer[:size] = self._data[self._position : self._position + size]
            self._position += size
            return size

    def open_with_partial_raw_reads(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        if path == image and mode == "rb":
            stream = PartialReadStream(image_bytes)
            if buffering == 0:
                return stream
            return io.BufferedReader(stream)
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", open_with_partial_raw_reads)

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PRO, "1.2.15")
        await client.upload_image(image, chunk_size=3)

    assert state["accepted_uploads"] == [b"abc", b"def"]


async def test_pro_upload_rejects_existing_basename_before_post(tmp_path: Path) -> None:
    """Pro preflight rejects an existing /data path before any upload POST."""
    image = tmp_path / "occupied.iso"
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["pro_images"] = ["/data/occupied.iso"]
        _mark(client, HWVersion.PRO, "1.2.15")
        with pytest.raises(FileExistsError):
            await client.upload_image(image, chunk_size=4)

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image")
    ]


async def test_non_pro_upload_rejects_existing_basename_by_default(
    tmp_path: Path,
) -> None:
    """A non-Pro upload does not silently replace an existing image."""
    image = tmp_path / "occupied.iso"
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["pro_images"] = ["/data/occupied.iso"]
        _mark(client, HWVersion.PCIE, "2.5.2")
        with pytest.raises(FileExistsError):
            await client.upload_image(image)

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image")
    ]


async def test_non_pro_upload_can_explicitly_replace_existing_image(
    tmp_path: Path,
) -> None:
    """Explicit overwrite keeps the non-Pro firmware replacement contract."""
    image = tmp_path / "occupied.iso"
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["pro_images"] = ["/data/occupied.iso"]
        _mark(client, HWVersion.PCIE, "2.5.2")
        await client.upload_image(image, overwrite=True)

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image"),
        ("POST", "/api/download/file"),
    ]


async def test_pro_upload_deletes_existing_image_only_with_explicit_overwrite(
    tmp_path: Path,
) -> None:
    """Explicit Pro overwrite removes the old file before sending replacement chunks."""
    image = tmp_path / "occupied.iso"
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["pro_images"] = ["/data/occupied.iso"]
        _mark(client, HWVersion.PRO, "1.2.15")
        await client.upload_image(image, chunk_size=4, overwrite=True)

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image"),
        ("POST", "/api/storage/image/delete"),
        ("POST", "/api/storage/image/upload"),
        ("POST", "/api/storage/image/upload"),
        ("POST", "/api/storage/image/upload"),
    ]


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("unsafe@name.iso", "sanitized"),
        ("safe-name.bin", "\\.iso or \\.img"),
    ],
)
async def test_pro_upload_rejects_unlistable_filename_before_io(
    tmp_path: Path,
    filename: str,
    message: str,
) -> None:
    """Pro filenames must survive firmware sanitizing and remain listable."""
    image = tmp_path / filename
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PRO, "1.2.15")
        with pytest.raises(ValueError, match=message):
            await client.upload_image(image, chunk_size=4)

    assert state["calls"] == []


@pytest.mark.parametrize(
    ("replacement", "accepted_chunks", "expected_progress"),
    [(b"ab", 1, [0, 2]), (b"abcdefg", 2, [0, 2, 4])],
)
async def test_pro_upload_detects_source_truncation_or_growth_between_chunks(
    tmp_path: Path,
    replacement: bytes,
    accepted_chunks: int,
    expected_progress: list[int],
) -> None:
    """Pro stops before the next POST if the source changes during chunking."""
    image = tmp_path / "changing.iso"
    image.write_bytes(b"abcdef")
    events: list[models.ImageTransferProgress] = []

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["after_pro_upload"] = lambda: image.write_bytes(replacement)
        _mark(client, HWVersion.PRO, "1.2.15")
        with pytest.raises(OSError, match="image changed"):
            await client.upload_image(
                image,
                chunk_size=2,
                progress_callback=events.append,
            )

    expected_calls = (
        [("GET", "/api/storage/image")]
        + [("POST", "/api/storage/image/upload") for _ in range(accepted_chunks)]
        + [("POST", "/api/storage/image/delete")]
    )
    assert [(call["method"], call["path"]) for call in state["calls"]] == expected_calls
    assert state["calls"][-1]["payload"] == {"file": "/data/changing.iso"}
    assert [event.bytes_transferred for event in events] == expected_progress


async def test_pro_upload_failure_cleans_partial_remote_image(tmp_path: Path) -> None:
    """A later failed chunk POST deletes the partial /data image."""
    image = tmp_path / "partial.iso"
    image.write_bytes(b"abcdef")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["upload_http_statuses"] = [200, 500]
        _mark(client, HWVersion.PRO, "1.2.15")
        with pytest.raises(aiohttp.ClientResponseError):
            await client.upload_image(image, chunk_size=2)

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image"),
        ("POST", "/api/storage/image/upload"),
        ("POST", "/api/storage/image/upload"),
        ("POST", "/api/storage/image/delete"),
    ]
    assert state["calls"][-1]["payload"] == {"file": "/data/partial.iso"}


async def test_pro_upload_callback_failure_cleans_partial_and_preserves_error(
    tmp_path: Path,
) -> None:
    """Cleanup errors do not replace a callback failure after an accepted chunk."""
    image = tmp_path / "callback-failure.iso"
    image.write_bytes(b"abcdef")
    events: list[models.ImageTransferProgress] = []

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["http_status"]["/api/storage/image/delete"] = 500
        _mark(client, HWVersion.PRO, "1.2.15")

        def progress(item: models.ImageTransferProgress) -> None:
            events.append(item)
            if item.bytes_transferred == 2:
                raise RuntimeError("synthetic callback failure")

        with pytest.raises(RuntimeError, match="synthetic callback failure"):
            await client.upload_image(image, chunk_size=2, progress_callback=progress)

    assert [event.bytes_transferred for event in events] == [0, 2]
    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image"),
        ("POST", "/api/storage/image/upload"),
        ("POST", "/api/storage/image/delete"),
    ]
    assert state["calls"][-1]["payload"] == {"file": "/data/callback-failure.iso"}


async def test_pro_upload_cancellation_cleans_partial_remote_image(
    tmp_path: Path,
) -> None:
    """Task cancellation after one accepted chunk still triggers best-effort delete."""
    image = tmp_path / "cancelled.iso"
    image.write_bytes(b"abcdef")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PRO, "1.2.15")

        async def progress(item: models.ImageTransferProgress) -> None:
            if item.bytes_transferred == 2:
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)

        with pytest.raises(asyncio.CancelledError):
            await client.upload_image(image, chunk_size=2, progress_callback=progress)

    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image"),
        ("POST", "/api/storage/image/upload"),
        ("POST", "/api/storage/image/delete"),
    ]
    assert state["calls"][-1]["payload"] == {"file": "/data/cancelled.iso"}


async def test_empty_upload_is_rejected_before_device_io(
    tmp_path: Path,
) -> None:
    """An empty image cannot create an unusable remote file or false progress."""
    image = tmp_path / "empty.iso"
    image.write_bytes(b"")
    events: list[models.ImageTransferProgress] = []

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PRO, "1.2.15")
        with pytest.raises(ValueError, match="must not be empty"):
            await client.upload_image(image, progress_callback=events.append)

    assert state["calls"] == []
    assert events == []


@pytest.mark.parametrize(
    ("hardware", "version", "sha256"),
    [
        (None, "2.5.2", None),
        (HWVersion.UNKNOWN, "2.5.2", None),
        (HWVersion.PRO, "1.2.15", "c" * 64),
        (HWVersion.PCIE, "2.3.1", "c" * 64),
    ],
)
async def test_upload_rejects_unsupported_hardware_or_checksum_before_io(
    tmp_path: Path,
    hardware: HWVersion | None,
    version: str,
    sha256: str | None,
) -> None:
    """Unknown hardware and unavailable checksum contracts fail before POST."""
    image = tmp_path / "synthetic.iso"
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        if hardware is not None:
            _mark(client, hardware, version)
        with pytest.raises(NanoKVMNotSupportedError):
            await client.upload_image(image, sha256=sha256)

    assert state["calls"] == []


async def test_upload_validates_path_chunk_size_and_sha_before_io(
    tmp_path: Path,
) -> None:
    """Local upload validation errors do not trigger version or upload requests."""
    image = tmp_path / "synthetic.iso"
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PCIE, "2.5.2")
        with pytest.raises(FileNotFoundError):
            await client.upload_image(tmp_path / "missing.iso")
        with pytest.raises(ValueError, match="regular file"):
            await client.upload_image(tmp_path)
        with pytest.raises(ValueError, match="chunk_size"):
            await client.upload_image(image, chunk_size=0)
        with pytest.raises(ValueError, match="64 hexadecimal"):
            await client.upload_image(image, sha256="not-a-checksum")

    assert state["calls"] == []


@pytest.mark.parametrize(
    "filename",
    ["synthetic image.iso", "synthetic-é.iso", "synthetic.img", "synthetic..iso"],
)
async def test_non_pro_upload_rejects_unaccepted_filenames_before_io(
    tmp_path: Path,
    filename: str,
) -> None:
    """Non-Pro filenames match the 2.5.2 ASCII .iso contract before I/O."""
    image = tmp_path / filename
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PCIE, "2.5.2")
        with pytest.raises(ValueError):
            await client.upload_image(image)

    assert state["calls"] == []


@pytest.mark.parametrize("hardware", [HWVersion.PCIE, HWVersion.PRO])
async def test_upload_post_disables_total_timeout_but_keeps_socket_timeouts(
    tmp_path: Path,
    hardware: HWVersion,
) -> None:
    """Upload requests have no total deadline and retain socket timeout bounds."""
    image = tmp_path / "timeout-check.iso"
    image.write_bytes(b"synthetic")
    captured_timeouts: list[aiohttp.ClientTimeout | None] = []

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token", request_timeout=17) as client,
    ):
        _mark(client, hardware, "1.2.15" if hardware is HWVersion.PRO else "2.5.2")
        original_request_form = client._session.api_request_form

        async def record_timeout(*args: Any, **kwargs: Any) -> Any:
            captured_timeouts.append(kwargs.get("timeout"))
            return await original_request_form(*args, **kwargs)

        with patch.object(client._session, "api_request_form", new=record_timeout):
            await client.upload_image(image, chunk_size=1024)

    assert len(captured_timeouts) == 1
    timeout = captured_timeouts[0]
    assert isinstance(timeout, aiohttp.ClientTimeout)
    assert timeout.total is None
    assert timeout.sock_connect == 17
    assert timeout.sock_read == 17


async def test_same_client_serializes_upload_preflight_and_transfer(
    tmp_path: Path,
) -> None:
    """A second Pro upload waits for the first preflight, transfer and cleanup."""
    first_image = tmp_path / "first" / "same.iso"
    second_image = tmp_path / "second" / "same.iso"
    first_image.parent.mkdir()
    second_image.parent.mkdir()
    first_image.write_bytes(b"first")
    second_image.write_bytes(b"second")
    first_post_started = asyncio.Event()
    release_first_post = asyncio.Event()
    get_images_calls = 0
    post_calls = 0

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PRO, "1.2.15")
        original_get_images = client._storage_controller.get_images
        original_request_form = client._session.api_request_form

        async def count_get_images() -> models.GetImagesRsp:
            nonlocal get_images_calls
            get_images_calls += 1
            return await original_get_images()

        async def pause_first_post(*args: Any, **kwargs: Any) -> Any:
            nonlocal post_calls
            post_calls += 1
            if post_calls == 1:
                first_post_started.set()
                await release_first_post.wait()
            return await original_request_form(*args, **kwargs)

        with (
            patch.object(
                client._storage_controller, "get_images", new=count_get_images
            ),
            patch.object(client._session, "api_request_form", new=pause_first_post),
        ):
            first_task = asyncio.create_task(client.upload_image(first_image))
            await first_post_started.wait()
            second_task = asyncio.create_task(client.upload_image(second_image))
            await asyncio.sleep(0)
            preflight_calls_while_first_paused = get_images_calls
            release_first_post.set()
            first_result, second_result = await asyncio.gather(
                first_task, second_task, return_exceptions=True
            )

    assert preflight_calls_while_first_paused == 1
    assert first_result is None
    assert isinstance(second_result, FileExistsError)
    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image"),
        ("POST", "/api/storage/image/upload"),
        ("GET", "/api/storage/image"),
    ]


async def test_image_upload_403_keeps_session_controller_error_classification(
    tmp_path: Path,
) -> None:
    """Image uploads keep the shared multipart 403 permission error mapping."""
    image = tmp_path / "synthetic.iso"
    image.write_bytes(b"synthetic")

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["http_status"]["/api/download/file"] = 403
        _mark(client, HWVersion.PCIE, "2.5.2")
        with pytest.raises(NanoKVMPermissionError) as exc_info:
            await client.upload_image(image)

    assert exc_info.value.status == 403
    assert exc_info.value.path == "/download/file"


async def test_rejected_non_pro_upload_does_not_report_100_percent(
    tmp_path: Path,
) -> None:
    """A non-success HTTP response suppresses the final accepted-byte update."""
    image = tmp_path / "synthetic.iso"
    image.write_bytes(b"1234")
    events: list[models.ImageTransferProgress] = []

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        state["http_status"]["/api/download/file"] = 500
        _mark(client, HWVersion.PCIE, "2.5.2")
        with pytest.raises(aiohttp.ClientResponseError):
            await client.upload_image(
                image, chunk_size=2, progress_callback=events.append
            )

    assert [event.bytes_transferred for event in events] == [0, 2]
    assert all(event.percentage < 100 for event in events)


async def test_progress_callback_failure_stops_upload_without_final_update(
    tmp_path: Path,
) -> None:
    """A callback failure aborts the stream without reporting total bytes."""
    image = tmp_path / "synthetic.iso"
    image.write_bytes(b"1234")
    events: list[models.ImageTransferProgress] = []

    async with (
        _device_server() as (base_url, state),
        NanoKVMClient(base_url, token="synthetic-token") as client,
    ):
        _mark(client, HWVersion.PCIE, "2.5.2")

        def progress(item: models.ImageTransferProgress) -> None:
            events.append(item)
            if item.bytes_transferred == 0:
                raise RuntimeError("synthetic callback failure")

        with pytest.raises(RuntimeError, match="synthetic callback failure"):
            await client.upload_image(image, chunk_size=2, progress_callback=progress)

    assert [event.bytes_transferred for event in events] == [0]
    assert all(event.percentage < 100 for event in events)
    assert [(call["method"], call["path"]) for call in state["calls"]] == [
        ("GET", "/api/storage/image")
    ]
