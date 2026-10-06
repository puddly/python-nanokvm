"""Internal script, image and download operations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
import contextlib
import functools
import inspect
from os import PathLike
from pathlib import Path
import re
import stat
from typing import Any

import aiohttp
from aiohttp import hdrs
from aiohttp.payload import Payload

from ..compatibility import F, require_application_version, require_hardware
from ..exceptions import NanoKVMNotSupportedError
from ..models.common import (
    DeleteImageReq,
    DeleteScriptReq,
    DownloadImageReq,
    DownloadStatus,
    GetImagesRsp,
    GetMountedImageRsp,
    GetScriptsRsp,
    HWFamily,
    ImageEnabledRsp,
    ImageTransferProgress,
    MountImageReq,
    RunScriptReq,
    RunScriptRsp,
    RunScriptType,
    StatusImageRsp,
    UploadScriptRsp,
)
from ..models.non_pro import GetCdRomRsp
from ..utils import _validate_sha256
from .session import Controller

ImageTransferProgressCallback = Callable[
    [ImageTransferProgress], None | Awaitable[None]
]


async def _report_image_transfer_progress(
    callback: ImageTransferProgressCallback | None,
    bytes_transferred: int,
    total_bytes: int,
) -> None:
    """Invoke a synchronous or asynchronous image progress callback."""
    if callback is None:
        return

    percentage = 100 * bytes_transferred / total_bytes if total_bytes else 0
    result = callback(
        ImageTransferProgress(
            bytes_transferred=bytes_transferred,
            total_bytes=total_bytes,
            percentage=percentage,
        )
    )
    if inspect.isawaitable(result):
        await result


class _ImageProgressPayload(Payload):
    """Stream one multipart image field in bounded chunks with progress updates."""

    def __init__(
        self,
        file_path: Path,
        *,
        chunk_size: int,
        total_bytes: int,
        report_progress: Callable[[int], Awaitable[None]],
    ) -> None:
        super().__init__(file_path, content_type="application/octet-stream")
        self._file_path = file_path
        self._chunk_size = chunk_size
        self._total_bytes = total_bytes
        self._size = total_bytes
        self._report_progress = report_progress

    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        """A file payload has no eager string representation."""
        return "<streamed image>"

    async def write(self, writer: Any) -> None:
        """Write no more than one configured chunk at a time."""
        self._consumed = True
        bytes_transferred = 0
        with self._file_path.open("rb") as image_file:
            while bytes_transferred < self._total_bytes:
                chunk = await asyncio.to_thread(
                    image_file.read,
                    min(self._chunk_size, self._total_bytes - bytes_transferred),
                )
                if not chunk:
                    raise OSError("image changed while it was being uploaded")

                await writer.write(chunk)
                bytes_transferred += len(chunk)
                if bytes_transferred < self._total_bytes:
                    await self._report_progress(bytes_transferred)

            if image_file.read(1):
                raise OSError("image changed while it was being uploaded")


def _validate_sha256_argument(func: F) -> F:
    """Validate a keyword-only ``sha256`` argument before version checks."""

    @functools.wraps(func)
    async def wrapper(self: StorageController, *args: Any, **kwargs: Any) -> Any:
        _validate_sha256(kwargs.get("sha256"))
        return await func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class StorageController(Controller):
    """Implement storage operations behind the public client facade."""

    async def get_scripts(self) -> GetScriptsRsp:
        """Get the list of uploaded scripts."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/vm/script",
            response_model=GetScriptsRsp,
        )

    async def upload_script(self, file_path: str | PathLike[str]) -> UploadScriptRsp:
        """Upload a script file."""
        return await self._session.upload_file(
            "/vm/script/upload",
            file_path,
            response_model=UploadScriptRsp,
        )

    async def run_script(self, name: str, script_type: RunScriptType) -> RunScriptRsp:
        """Run an uploaded script."""
        return await self._session.api_request_json(
            hdrs.METH_POST,
            "/vm/script/run",
            response_model=RunScriptRsp,
            data=RunScriptReq(name=name, type=script_type),
        )

    async def delete_script(self, name: str) -> None:
        """Delete an uploaded script."""
        await self._session.api_request_json(
            hdrs.METH_DELETE,
            "/vm/script",
            data=DeleteScriptReq(name=name),
        )

    async def get_images(self) -> GetImagesRsp:
        """Get the list of available image files."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/storage/image",
            response_model=GetImagesRsp,
        )

    async def upload_image(
        self,
        file_path: str | PathLike[str],
        *,
        progress_callback: ImageTransferProgressCallback | None = None,
        sha256: str | None = None,
        chunk_size: int = 1024 * 1024,
        overwrite: bool = False,
    ) -> None:
        """Upload an image, rejecting an existing name unless overwrite is enabled.

        The image is streamed on non-Pro and sent in chunks on Pro.
        On Pro, overwriting deletes the existing image before the upload
        starts, so a failed upload leaves neither copy.
        """
        async with self._session._image_transfer_lock:
            image_path = Path(file_path)
            file_stat = image_path.stat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("file_path must point to a regular file")
            if chunk_size <= 0:
                raise ValueError("chunk_size must be a positive integer")
            _validate_sha256(sha256)

            if (
                self._session._hw_version is None
                or self._session._hw_version.family is None
            ):
                raise NanoKVMNotSupportedError(
                    "upload_image requires detected supported hardware"
                )

            total_bytes = file_stat.st_size
            if total_bytes == 0:
                raise ValueError("file_path must not be empty")

            is_pro = self._session.is_hardware_family(HWFamily.PRO)
            if is_pro and sha256 is not None:
                raise NanoKVMNotSupportedError(
                    "upload_image does not support sha256 on NanoKVM Pro"
                )

            transfer_timeout = aiohttp.ClientTimeout(
                total=None,
                sock_connect=self._session._request_timeout,
                sock_read=self._session._request_timeout,
            )

            if not is_pro:
                if ".." in image_path.name:
                    raise ValueError("non-Pro image filename must not contain '..'")
                if re.fullmatch(r"[A-Za-z0-9._-]+", image_path.name) is None:
                    raise ValueError(
                        "non-Pro image filename must use ASCII letters, numbers, "
                        "dot, underscore, or hyphen"
                    )
                if not image_path.name.lower().endswith(".iso"):
                    raise ValueError("non-Pro image filename must end with .iso")

                await self._ensure_image_transfer_version("2.3.1")
                if sha256 is not None:
                    await self._ensure_image_transfer_version("2.5.0")
            else:
                if not all(
                    character in " -_." or character.isalnum()
                    for character in image_path.name
                ):
                    raise ValueError(
                        "Pro image filename must already be sanitized by firmware rules"
                    )
                if not image_path.name.lower().endswith((".iso", ".img")):
                    raise ValueError("Pro image filename must end with .iso or .img")

            remote_file = f"/data/{image_path.name}"
            remote_images = await self.get_images()
            remote_file_exists = remote_file in remote_images.files
            if remote_file_exists and not overwrite:
                raise FileExistsError(
                    f"NanoKVM already contains an image named {image_path.name}"
                )
            if remote_file_exists and is_pro:
                await self.delete_image(remote_file)

            await _report_image_transfer_progress(progress_callback, 0, total_bytes)

            if is_pro:
                total_chunks = max(1, (total_bytes + chunk_size - 1) // chunk_size)
                bytes_transferred = 0
                upload_started = False
                try:
                    with image_path.open("rb", buffering=0) as image_file:
                        for chunk_index in range(total_chunks):
                            expected_size = min(
                                chunk_size, total_bytes - chunk_index * chunk_size
                            )
                            chunk_parts: list[bytes] = []
                            bytes_read = 0
                            while bytes_read < expected_size:
                                part = await asyncio.to_thread(
                                    image_file.read, expected_size - bytes_read
                                )
                                if not part:
                                    break
                                chunk_parts.append(part)
                                bytes_read += len(part)
                            chunk = b"".join(chunk_parts)
                            if len(chunk) != expected_size:
                                raise OSError(
                                    "image changed while it was being uploaded"
                                )
                            if chunk_index == total_chunks - 1 and image_file.read(1):
                                raise OSError(
                                    "image changed while it was being uploaded"
                                )

                            form = aiohttp.FormData(quote_fields=False)
                            form.add_field("chunkIndex", str(chunk_index))
                            form.add_field("chunkSize", str(chunk_size))
                            form.add_field("totalChunks", str(total_chunks))
                            form.add_field(
                                "file",
                                chunk,
                                filename=image_path.name,
                                content_type="application/octet-stream",
                            )
                            upload_started = True
                            await self._session.api_request_json(
                                hdrs.METH_POST,
                                "/storage/image/upload",
                                data=form,
                                timeout=transfer_timeout,
                            )
                            bytes_transferred += len(chunk)
                            await _report_image_transfer_progress(
                                progress_callback, bytes_transferred, total_bytes
                            )
                except BaseException:
                    if upload_started:
                        with contextlib.suppress(BaseException):
                            await asyncio.shield(self.delete_image(remote_file))
                    raise
                return

            async def report_progress(bytes_transferred: int) -> None:
                await _report_image_transfer_progress(
                    progress_callback, bytes_transferred, total_bytes
                )

            form = aiohttp.FormData()
            form.add_field(
                "file",
                _ImageProgressPayload(
                    image_path,
                    chunk_size=chunk_size,
                    total_bytes=total_bytes,
                    report_progress=report_progress,
                ),
                filename=image_path.name,
                content_type="application/octet-stream",
            )
            headers = {"X-SHA256-Sum": sha256} if sha256 is not None else {}
            await self._session.api_request_json(
                hdrs.METH_POST,
                "/download/file",
                data=form,
                headers=headers,
                timeout=transfer_timeout,
            )
            await _report_image_transfer_progress(
                progress_callback, total_bytes, total_bytes
            )

    async def get_mounted_image(self) -> GetMountedImageRsp:
        """Get the currently mounted image file."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/storage/image/mounted",
            response_model=GetMountedImageRsp,
        )

    async def mount_image(
        self,
        file: str | None = None,
        cdrom: bool = False,
        *,
        read_only: bool = False,  # Pro only
    ) -> None:
        """Mount an image file or unmount if file is None.

        ``read_only`` needs NanoKVM Pro application 1.2.1 or newer.
        """
        if (
            file
            and read_only
            and not await self._session.application_version_at_least(pro="1.2.1")
        ):
            raise NanoKVMNotSupportedError(
                "mount_image read_only requires Pro application version >= 1.2.1 "
                f"(detected: {self._session._application_version})"
            )
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/storage/image/mount",
            data=MountImageReq.model_validate(
                {
                    "file": file,
                    "cdrom": cdrom if file else None,
                    "readOnly": read_only if file else None,
                }
            ),
        )

    @require_application_version(non_pro="2.3.0", pro="1.1.6")
    async def delete_image(self, file: str) -> None:
        """Delete an image file."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/storage/image/delete",
            data=DeleteImageReq(file=file),
        )

    @require_hardware(HWFamily.NON_PRO)
    @require_application_version(non_pro="2.2.1")
    async def get_cdrom_status(self) -> GetCdRomRsp:
        """Check if the mounted image is in CD-ROM mode."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/storage/cdrom",
            response_model=GetCdRomRsp,
        )

    def _image_download_prefix(self) -> str:
        """Return the image download route prefix for the detected hardware."""
        if self._session.is_hardware_family(HWFamily.PRO):
            return "/storage/download"
        return "/download"

    @require_application_version(non_pro="2.1.6")
    async def is_image_download_enabled(self) -> ImageEnabledRsp:
        """Check if the /data partition allows downloads."""
        prefix = self._image_download_prefix()
        return await self._session.api_request_json(
            hdrs.METH_GET,
            f"{prefix}/image/enabled",
            response_model=ImageEnabledRsp,
        )

    @require_application_version(non_pro="2.1.6")
    async def get_image_download_status(self) -> StatusImageRsp:
        """Get the status of an ongoing image download."""
        prefix = self._image_download_prefix()
        return await self._session.api_request_json(
            hdrs.METH_GET,
            f"{prefix}/image/status",
            response_model=StatusImageRsp,
        )

    @require_application_version(non_pro="2.5.0")
    @require_hardware(HWFamily.NON_PRO)
    async def cancel_image_download(self) -> None:
        """Cancel an active non-Pro image download."""
        await self._session.api_request_json(hdrs.METH_POST, "/download/image/cancel")

    async def watch_image_download(
        self, *, poll_interval: float = 1.0
    ) -> AsyncGenerator[StatusImageRsp, None]:
        """Poll image-download status until it reaches a terminal state."""
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than zero")

        terminal_statuses = {
            DownloadStatus.IDLE,
            DownloadStatus.SUCCESS,
            DownloadStatus.FAILED,
            DownloadStatus.CHECKSUM_FAILED,
        }
        while True:
            status = await self.get_image_download_status()
            yield status
            if status.status in terminal_statuses:
                return
            await asyncio.sleep(poll_interval)

    @_validate_sha256_argument
    @require_application_version(non_pro="2.1.6")
    async def download_image(
        self, url: str, *, sha256: str | None = None
    ) -> StatusImageRsp:
        """Start downloading an image from a URL."""
        if sha256 is not None:
            if (
                self._session._hw_version is None
                or self._session._hw_version.family is not HWFamily.NON_PRO
            ):
                raise NanoKVMNotSupportedError(
                    "download_image sha256 is only supported on non-Pro hardware"
                )
            await self._ensure_image_transfer_version("2.5.0")

        prefix = self._image_download_prefix()
        return await self._session.api_request_json(
            hdrs.METH_POST,
            f"{prefix}/image",
            response_model=StatusImageRsp,
            data=DownloadImageReq(file=url, sha256sum=sha256),
        )

    async def _ensure_image_transfer_version(self, minimum: str) -> None:
        """Check a non-Pro image-transfer firmware minimum."""
        if not await self._session.application_version_at_least(non_pro=minimum):
            raise NanoKVMNotSupportedError(
                f"image transfer requires non-Pro application version >= {minimum} "
                f"(detected: {self._session._application_version})"
            )
