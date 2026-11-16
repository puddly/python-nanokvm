"""Tests for the nanokvm command-line interface."""

from __future__ import annotations

from collections.abc import Iterator
import getpass
from typing import Any

import pytest

from nanokvm import cli
from nanokvm.client import NanoKVMApiError, NanoKVMNotSupportedError
from nanokvm.models import (
    GetGpioRsp,
    GetHardwareRsp,
    GetImagesRsp,
    GetInfoRsp,
    GetMediaRsp,
    GetMountedImageRsp,
    GpioType,
    HWVersion,
)

_URL = "http://kvm.example.test/api/"


def _medium(rel: str, *, attached: bool) -> dict[str, Any]:
    return {
        "type": "image" if rel else "partition",
        "rel": rel,
        "name": rel or "Shared folder",
        "size": 0,
        "fs": "iso9660" if rel else "exfat",
        "browsable": True,
        "attached": attached,
        "modifiedAt": "2026-11-16T00:00:00Z",
    }


class _FakeKVM:
    """Stands in for NanoKVMSyncClient and records what the command does."""

    instances: list[_FakeKVM] = []
    # Power LED readings in order; the last one repeats.
    power_leds: list[bool] = [False]
    login_error: Exception | None = None
    # Whether the device has the non-Pro 2.5.3 media API; without it, the
    # client refuses those calls and only the image API is left.
    media_api = True

    def __init__(self, url: str, **kwargs: Any) -> None:
        self.url = url
        self.kwargs = kwargs
        self.calls: list[tuple[Any, ...]] = []
        self.power_leds = list(type(self).power_leds)
        _FakeKVM.instances.append(self)

    def __enter__(self) -> _FakeKVM:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.calls.append(("close",))

    def authenticate(self, username: str, password: str) -> None:
        self.calls.append(("authenticate", username, password))
        if self.login_error is not None:
            raise self.login_error

    def get_hardware(self) -> GetHardwareRsp:
        return GetHardwareRsp(version=HWVersion.PCIE)

    def get_info(self) -> GetInfoRsp:
        return GetInfoRsp.model_validate(
            {
                "ips": [],
                "mdns": "kvm",
                "image": "v1.4.2",
                "application": "2.5.2",
                "deviceKey": "synthetic",
            }
        )

    def get_gpio(self) -> GetGpioRsp:
        self.calls.append(("get_gpio",))
        pwr = self.power_leds.pop(0) if len(self.power_leds) > 1 else self.power_leds[0]
        return GetGpioRsp(pwr=pwr, hdd=False)

    def push_button(self, button: GpioType, duration_ms: int) -> None:
        self.calls.append(("push_button", button, duration_ms))

    def _require_media_api(self) -> None:
        if not self.media_api:
            raise NanoKVMNotSupportedError("requires non-Pro 2.5.3")

    def get_media(self) -> GetMediaRsp:
        self._require_media_api()
        return GetMediaRsp.model_validate(
            {
                "media": [
                    _medium("debian.iso", attached=False),
                    _medium("isos/win.iso", attached=True),
                    _medium("", attached=False),
                ],
                "capabilities": {"loop": True},
            }
        )

    def mount_media(
        self, rel: str | None = None, *, cdrom: bool = False, force: bool = False
    ) -> None:
        self._require_media_api()
        self.calls.append(("mount_media", rel, cdrom, force))

    def eject_media(self, *, force: bool = False) -> None:
        self._require_media_api()
        self.calls.append(("eject_media", force))

    def get_images(self) -> GetImagesRsp:
        return GetImagesRsp(files=["/data/debian.iso", "/data/win.iso"])

    def get_mounted_image(self) -> GetMountedImageRsp:
        return GetMountedImageRsp(file="/data/win.iso")

    def mount_image(self, file: str | None = None, cdrom: bool = False) -> None:
        self.calls.append(("mount_image", file, cdrom))


@pytest.fixture(autouse=True)
def fake_kvm(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the client and the device settings for every test."""
    _FakeKVM.instances = []
    monkeypatch.setattr(_FakeKVM, "power_leds", [False])
    monkeypatch.setattr(_FakeKVM, "login_error", None)
    monkeypatch.setattr(_FakeKVM, "media_api", True)
    monkeypatch.setattr(cli, "NanoKVMSyncClient", _FakeKVM)
    monkeypatch.setattr(cli, "_POLL_INTERVAL", 0)
    monkeypatch.setenv("NANOKVM_URL", _URL)
    monkeypatch.setenv("NANOKVM_USERNAME", "synthetic-user")
    monkeypatch.setenv("NANOKVM_PASSWORD", "synthetic-password")
    yield


def _kvm() -> _FakeKVM:
    (kvm,) = _FakeKVM.instances
    return kvm


def _presses(kvm: _FakeKVM) -> list[tuple[Any, ...]]:
    return [call[1:] for call in kvm.calls if call[0] == "push_button"]


def _led_reads(kvm: _FakeKVM) -> int:
    return kvm.calls.count(("get_gpio",))


def _mounts(kvm: _FakeKVM) -> list[tuple[Any, ...]]:
    return [
        call
        for call in kvm.calls
        if call[0] in {"mount_media", "eject_media", "mount_image"}
    ]


def test_info_logs_in_with_the_environment_and_prints_versions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The device and login come from the environment."""
    assert cli.main(["info"]) == 0

    kvm = _kvm()
    assert kvm.url == _URL
    assert kvm.kwargs == {"verify_ssl": True, "ssl_ca_cert": None}
    assert kvm.calls[0] == ("authenticate", "synthetic-user", "synthetic-password")
    assert kvm.calls[-1] == ("close",)
    assert capsys.readouterr().out == (
        "hardware: PCIE\napplication: 2.5.2\nimage: v1.4.2\n"
    )


def test_options_override_the_environment() -> None:
    """--url, --username and the HTTPS options reach the client."""
    cli.main(
        [
            "--url",
            "https://other.example.test/api/",
            "--username",
            "other-user",
            "--ca-cert",
            "ca.pem",
            "--insecure",
            "status",
        ]
    )

    kvm = _kvm()
    assert kvm.url == "https://other.example.test/api/"
    assert kvm.kwargs == {"verify_ssl": False, "ssl_ca_cert": "ca.pem"}
    assert kvm.calls[0] == ("authenticate", "other-user", "synthetic-password")


def test_the_password_is_asked_for_when_not_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The password never has to appear on the command line."""
    monkeypatch.delenv("NANOKVM_PASSWORD")
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return "typed"

    monkeypatch.setattr(getpass, "getpass", ask)

    cli.main(["status"])

    assert prompts == ["Password for synthetic-user: "]
    assert _kvm().calls[0] == ("authenticate", "synthetic-user", "typed")


def test_there_is_no_password_option() -> None:
    """A password option would show the password to other local users."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--password", "secret", "status"])

    assert exc_info.value.code == 2


def test_the_url_is_required(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a device URL the command stops before connecting."""
    monkeypatch.delenv("NANOKVM_URL")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["info"])

    assert exc_info.value.code == 2
    assert "NANOKVM_URL" in capsys.readouterr().err
    assert not _FakeKVM.instances


def test_status_prints_the_leds(capsys: pytest.CaptureFixture[str]) -> None:
    """The LEDs read as on or off."""
    assert cli.main(["status"]) == 0

    assert capsys.readouterr().out == "power LED: off\nHDD LED: off\n"


@pytest.mark.parametrize(
    ("argv", "press"),
    [
        (["power", "press"], (GpioType.POWER, 800)),
        (["power", "press", "--ms", "1500"], (GpioType.POWER, 1500)),
        (["reset"], (GpioType.RESET, 800)),
        (["reset", "--ms", "200"], (GpioType.RESET, 200)),
    ],
)
def test_button_presses(argv: list[str], press: tuple[GpioType, int]) -> None:
    """Each press command holds the button for the requested time."""
    assert cli.main(argv) == 0

    assert _presses(_kvm()) == [press]


def test_power_off_forces_the_target_off_and_waits_for_the_led(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 6 s hold, then the power LED is polled until it goes off."""
    monkeypatch.setattr(_FakeKVM, "power_leds", [True, True, True, False])

    assert cli.main(["power", "off"]) == 0

    assert _presses(_kvm()) == [(GpioType.POWER, 6000)]
    assert _led_reads(_kvm()) == 4
    assert capsys.readouterr().out == "off\n"


def test_power_on_presses_briefly_and_waits_for_the_led(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A short press turns the target on."""
    monkeypatch.setattr(_FakeKVM, "power_leds", [False, False, True])

    assert cli.main(["power", "on"]) == 0

    assert _presses(_kvm()) == [(GpioType.POWER, 800)]
    assert _led_reads(_kvm()) == 3
    assert capsys.readouterr().out == "on\n"


@pytest.mark.parametrize(("action", "led"), [("on", True), ("off", False)])
def test_power_does_nothing_when_already_in_that_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
    led: bool,
) -> None:
    """A second 'power on' must not press the button and turn it off."""
    monkeypatch.setattr(_FakeKVM, "power_leds", [led])

    assert cli.main(["power", action]) == 0

    assert _presses(_kvm()) == []
    assert capsys.readouterr().out == f"already {action}\n"


def test_power_off_fails_when_the_led_stays_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wait is bounded by --timeout."""
    monkeypatch.setattr(_FakeKVM, "power_leds", [True])

    assert cli.main(["power", "off", "--timeout", "0.05"]) == 1

    assert "not off after 0.05 s" in capsys.readouterr().err


def test_media_lists_images_and_the_data_partition(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """From non-Pro 2.5.3, images go by their path in the media directory."""
    assert cli.main(["media"]) == 0

    assert capsys.readouterr().out == (
        "  debian.iso\n* isos/win.iso\n  (data partition)\n"
    )


def test_media_lists_images_on_the_image_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Older firmware and the Pro list images by their full path."""
    monkeypatch.setattr(_FakeKVM, "media_api", False)

    assert cli.main(["media"]) == 0

    assert capsys.readouterr().out == "  /data/debian.iso\n* /data/win.iso\n"


@pytest.mark.parametrize(
    ("argv", "media_api", "calls"),
    [
        (["mount", "debian.iso"], True, [("mount_media", "debian.iso", False, False)]),
        (
            ["mount", "debian.iso", "--cdrom", "--force"],
            True,
            [("mount_media", "debian.iso", True, True)],
        ),
        (["mount"], True, [("mount_media", None, False, False)]),
        (["unmount"], True, [("eject_media", False)]),
        (["unmount", "--force"], True, [("eject_media", True)]),
        (
            ["mount", "/data/debian.iso", "--cdrom"],
            False,
            [("mount_image", "/data/debian.iso", True)],
        ),
        (["unmount"], False, [("mount_image", None, False)]),
    ],
)
def test_mount_and_unmount(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    media_api: bool,
    calls: list[tuple[Any, ...]],
) -> None:
    """The media API from non-Pro 2.5.3, the image API before it and on the Pro."""
    monkeypatch.setattr(_FakeKVM, "media_api", media_api)

    assert cli.main(argv) == 0

    assert _mounts(_kvm()) == calls


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        (["mount"], "mounting the data partition needs non-Pro 2.5.3"),
        (["mount", "/data/debian.iso", "--force"], "--force needs non-Pro 2.5.3"),
        (["unmount", "--force"], "--force needs non-Pro 2.5.3"),
    ],
)
def test_the_image_api_refuses_media_api_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    error: str,
) -> None:
    """Without the media API, nothing is mounted or unmounted instead."""
    monkeypatch.setattr(_FakeKVM, "media_api", False)

    assert cli.main(argv) == 1

    assert _mounts(_kvm()) == []
    assert error in capsys.readouterr().err


def test_device_errors_exit_with_a_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Errors print one line, with the device's reason, instead of a traceback."""
    monkeypatch.setattr(
        _FakeKVM,
        "login_error",
        NanoKVMApiError(
            "API returned error (Code: -11)",
            code=-11,
            msg="the target machine is using the medium",
        ),
    )

    assert cli.main(["status"]) == 1

    assert capsys.readouterr().err == (
        "nanokvm: error: API returned error (Code: -11): "
        "the target machine is using the medium\n"
    )
