"""Internal virtual media operations (non-Pro 2.5.3 storage API)."""

from __future__ import annotations

import asyncio
from os import PathLike
from pathlib import Path

from aiohttp import ClientTimeout, hdrs

from ..compatibility import require_hardware
from ..exceptions import NanoKVMInvalidResponseError
from ..models.common import HWFamily
from ..models.non_pro import (
    EjectMediaReq,
    GetMediaFilesRsp,
    GetMediaRsp,
    GetMediaStateRsp,
    GetMediaStatusRsp,
    MediaFile,
    MediaPathReq,
    MediaRefReq,
    MediaType,
    MountMediaReq,
)
from .session import Controller


def _media_type(rel: str | None) -> MediaType:
    # An image is named by its path in the media directory; no name is the
    # shared data partition.
    return MediaType.IMAGE if rel else MediaType.PARTITION


def _media_params(rel: str | None, **extra: str) -> dict[str, str]:
    return {"type": _media_type(rel).value, "rel": rel or "", **extra}


class MediaController(Controller):
    """Implement virtual media operations behind the public client facade.

    ``rel`` names an image by its path relative to the media directory, as
    ``get_media()`` lists it. Leaving it out means the shared data partition.
    """

    def _transfer_timeout(self) -> ClientTimeout:
        return ClientTimeout(
            total=None,
            sock_connect=self._session._request_timeout,
            sock_read=self._session._request_timeout,
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def get_media(self) -> GetMediaRsp:
        """List the images and the data partition the USB disk can carry."""
        return await self._session.api_request_json(
            hdrs.METH_GET, "/storage/media", response_model=GetMediaRsp
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def get_media_state(self) -> GetMediaStateRsp:
        """Get what the target machine currently sees on the USB disk."""
        return await self._session.api_request_json(
            hdrs.METH_GET, "/storage/media/state", response_model=GetMediaStateRsp
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def get_media_status(self) -> GetMediaStatusRsp:
        """Get the progress of the running media file transfer."""
        return await self._session.api_request_json(
            hdrs.METH_GET, "/storage/media/status", response_model=GetMediaStatusRsp
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def mount_media(
        self, rel: str | None = None, *, cdrom: bool = False, force: bool = False
    ) -> None:
        """Attach an image, or the data partition, to the target machine.

        An image is always served read-only. ``cdrom`` needs an ISO 9660 or
        UDF image. ``force`` takes the disk back from a target machine that
        holds it, losing its unflushed writes.
        """
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/storage/media/mount",
            data=MountMediaReq(
                type=_media_type(rel), rel=rel or "", cdrom=cdrom, force=force
            ),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def eject_media(self, *, force: bool = False) -> None:
        """Release the attached medium; ``force`` as in ``mount_media``."""
        await self._session.api_request_json(
            hdrs.METH_POST, "/storage/media/eject", data=EjectMediaReq(force=force)
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def delete_media(self, rel: str) -> None:
        """Delete an image. The data partition cannot be deleted."""
        if not rel:
            raise ValueError("rel must name an image")
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/storage/media/delete",
            data=MediaRefReq(type=MediaType.IMAGE, rel=rel),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def list_media_files(
        self, path: str = "/", *, rel: str | None = None
    ) -> GetMediaFilesRsp:
        """List a directory inside an image or the data partition."""
        return await self._session.api_request_json(
            hdrs.METH_GET,
            "/storage/media/files",
            response_model=GetMediaFilesRsp,
            params=_media_params(rel, path=path),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def upload_media_file(
        self,
        file_path: str | PathLike[str],
        path: str = "/",
        *,
        rel: str | None = None,
    ) -> MediaFile:
        """Upload a local file into a directory of an image or the partition.

        An existing name raises ``NanoKVMApiError`` with code -16.
        """
        return await self._session.upload_file(
            "/storage/media/files/upload",
            file_path,
            response_model=MediaFile,
            params=_media_params(rel, path=path),
            timeout=self._transfer_timeout(),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.4")
    async def create_media_directory(
        self, path: str, *, rel: str | None = None, exist_ok: bool = False
    ) -> None:
        """Create a directory inside an image or the data partition."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/storage/media/files/mkdir",
            data=MediaPathReq(
                type=_media_type(rel),
                rel=rel or "",
                path=path,
                exists_ok=exist_ok or None,
            ),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def delete_media_file(self, path: str, *, rel: str | None = None) -> None:
        """Delete a file or directory inside an image or the data partition."""
        await self._session.api_request_json(
            hdrs.METH_POST,
            "/storage/media/files/delete",
            data=MediaPathReq(type=_media_type(rel), rel=rel or "", path=path),
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def download_media_file(
        self,
        path: str,
        destination: str | PathLike[str],
        *,
        rel: str | None = None,
    ) -> None:
        """Save a file from inside an image or the data partition locally."""
        await self._download(
            "/storage/media/files/download", _media_params(rel, path=path), destination
        )

    @require_hardware(HWFamily.NON_PRO, min_version="2.5.3")
    async def download_media(self, rel: str, destination: str | PathLike[str]) -> None:
        """Save a whole image locally. An attached image is refused."""
        if not rel:
            raise ValueError("rel must name an image")
        await self._download("/storage/media/download", _media_params(rel), destination)

    async def _download(
        self, path: str, params: dict[str, str], destination: str | PathLike[str]
    ) -> None:
        target = Path(destination)
        async with self._session.request(
            hdrs.METH_GET, path, params=params, timeout=self._transfer_timeout()
        ) as response:
            # The device answers a refusal with its JSON envelope instead of
            # the file.
            if response.content_type == "application/json":
                self._session.validate_api_response(
                    await self._session.read_json_response(response)
                )
                raise NanoKVMInvalidResponseError("Expected a file, got JSON")
            try:
                with target.open("wb") as file:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        await asyncio.to_thread(file.write, chunk)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
