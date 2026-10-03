"""Domain components share a session without depending on the public facade."""

import logging

import aiohttp
from aioresponses import aioresponses
import pytest
import yarl

from nanokvm.client import NanoKVMClient
from nanokvm.components.hid import HidController
from nanokvm.components.network import NetworkController
from nanokvm.components.session import SessionController
from nanokvm.components.storage import StorageController
from nanokvm.exceptions import NanoKVMNotAuthenticatedError, NanoKVMPermissionError
from nanokvm.models import HWVersion


async def test_components_share_permissions_and_external_session() -> None:
    """A forbidden storage read leaves HID/network usable until a 401 clears them."""
    url = "http://kvm.local/api/"
    async with aiohttp.ClientSession() as http:
        session = SessionController(
            url,
            token="synthetic-token",
            session=http,
            logger=logging.getLogger(__name__),
        )
        session._hw_version = HWVersion.PCIE
        session._application_version = "2.5.1"
        hid = HidController(session)
        network = NetworkController(session)
        storage = StorageController(session)
        await session.enter()
        try:
            with aioresponses() as mocked:
                mocked.get(f"{url}storage/image", status=403, body='"forbidden"')
                mocked.post(
                    f"{url}hid/paste", payload={"code": 0, "msg": "ok", "data": None}
                )
                mocked.get(
                    f"{url}network/wol/mac",
                    payload={
                        "code": 0,
                        "msg": "ok",
                        "data": {"macs": ["00:11:22:33:44:55"]},
                    },
                )
                mocked.get(f"{url}network/wifi", status=401, body='"unauthorized"')

                with pytest.raises(NanoKVMPermissionError):
                    await storage.get_images()
                await hid.paste_text("Hello")
                assert (await network.get_wol_macs()).macs == ["00:11:22:33:44:55"]
                assert session._token == "synthetic-token"

                with pytest.raises(NanoKVMNotAuthenticatedError):
                    await network.get_wifi_status()
                assert session._token is None
                with pytest.raises(NanoKVMNotAuthenticatedError):
                    await hid.paste_text("Hello")
        finally:
            await session.exit()
        assert not http.closed


async def test_mutating_public_url_updates_all_components() -> None:
    """The writable URL still controls both metadata and domain requests."""
    old_url = "http://old-kvm.local/api/"
    new_url = "http://new-kvm.local/api/"
    async with NanoKVMClient(old_url, token="synthetic-token") as client:
        client.url = yarl.URL(new_url)
        with aioresponses() as mocked:
            mocked.get(
                f"{new_url}vm/hardware",
                payload={"code": 0, "msg": "ok", "data": {"version": "Pro"}},
            )
            mocked.get(
                f"{new_url}network/wol/mac",
                payload={"code": 0, "msg": "ok", "data": {"macs": []}},
            )
            assert (await client.get_hardware()).version is HWVersion.PRO
            assert (await client.get_wol_macs()).macs == []
