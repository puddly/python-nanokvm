"""Tests for the non-Pro 2.5.3 virtual media API."""

from pathlib import Path

from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMApiError, NanoKVMClient, NanoKVMNotSupportedError

from .common import BASE_URL, mark_detected

_OK = {"code": 0, "msg": "success", "data": None}


def _calls(mocked: aioresponses, method: str, path: str) -> list[object]:
    return [
        call.kwargs.get("json")
        for (m, url), calls in mocked.requests.items()
        if m == method and url.path == f"/api/{path}"
        for call in calls
    ]


async def test_rel_picks_image_or_partition(
    client: NanoKVMClient, mocked: aioresponses
) -> None:
    """No ``rel`` addresses the data partition, a ``rel`` an image."""
    mark_detected(client, application_version="2.5.3")
    mocked.post(f"{BASE_URL}storage/media/mount", payload=_OK, repeat=True)
    mocked.post(f"{BASE_URL}storage/media/eject", payload=_OK)
    mocked.post(f"{BASE_URL}storage/media/files/delete", payload=_OK)
    files = {"path": "/a", "entries": None, "free": 1, "total": 2, "writing": False}
    mocked.get(
        f"{BASE_URL}storage/media/files?path=/a&rel=debian.iso&type=image",
        payload={**_OK, "data": files},
    )

    await client.mount_media()
    await client.mount_media("debian.iso", cdrom=True)
    await client.eject_media(force=True)
    await client.delete_media_file("/a/b.txt")
    listing = await client.list_media_files("/a", rel="debian.iso")

    assert listing.entries == []
    assert _calls(mocked, "POST", "storage/media/mount") == [
        {"type": "partition", "rel": "", "cdrom": False, "force": False},
        {"type": "image", "rel": "debian.iso", "cdrom": True, "force": False},
    ]
    assert _calls(mocked, "POST", "storage/media/eject") == [{"force": True}]
    assert _calls(mocked, "POST", "storage/media/files/delete") == [
        {"type": "partition", "rel": "", "path": "/a/b.txt"}
    ]


async def test_versions(client: NanoKVMClient, mocked: aioresponses) -> None:
    """The media API needs 2.5.3 and directory creation 2.5.4."""
    mark_detected(client, application_version="2.5.2")
    with pytest.raises(NanoKVMNotSupportedError):
        await client.get_media()

    mark_detected(client, application_version="2.5.3")
    with pytest.raises(NanoKVMNotSupportedError):
        await client.create_media_directory("/new")
    assert not mocked.requests

    mark_detected(client, application_version="2.5.4")
    mocked.post(f"{BASE_URL}storage/media/files/mkdir", payload=_OK)
    await client.create_media_directory("/new", exist_ok=True)
    assert _calls(mocked, "POST", "storage/media/files/mkdir") == [
        {"type": "partition", "rel": "", "path": "/new", "existsOk": True}
    ]


async def test_download_writes_the_file(
    client: NanoKVMClient, mocked: aioresponses, tmp_path: Path
) -> None:
    """The body lands in the destination."""
    mark_detected(client)
    mocked.get(
        f"{BASE_URL}storage/media/files/download?path=/a.txt&rel=&type=partition",
        body=b"hello",
        content_type="application/octet-stream",
    )

    await client.download_media_file("/a.txt", tmp_path / "a.txt")

    assert (tmp_path / "a.txt").read_bytes() == b"hello"


async def test_download_refusal_raises_and_leaves_no_file(
    client: NanoKVMClient, mocked: aioresponses, tmp_path: Path
) -> None:
    """A JSON error envelope in place of the file raises the API error."""
    mark_detected(client)
    mocked.get(
        f"{BASE_URL}storage/media/download?rel=debian.iso&type=image",
        payload={"code": -11, "msg": "the medium is attached", "data": None},
    )

    with pytest.raises(NanoKVMApiError) as err:
        await client.download_media("debian.iso", tmp_path / "debian.iso")

    assert err.value.code == -11
    assert not (tmp_path / "debian.iso").exists()


async def test_upload_sends_the_target_directory(
    client: NanoKVMClient, mocked: aioresponses, tmp_path: Path
) -> None:
    """The directory and medium travel as query parameters."""
    mark_detected(client)
    source = tmp_path / "notes.txt"
    source.write_text("hi")
    url = f"{BASE_URL}storage/media/files/upload?path=/docs&rel=&type=partition"
    mocked.post(url, payload={**_OK, "data": {"name": "notes.txt", "size": 2}})

    uploaded = await client.upload_media_file(source, "/docs")

    assert (uploaded.name, uploaded.size) == ("notes.txt", 2)
    assert ("POST", yarl.URL(url)) in mocked.requests
