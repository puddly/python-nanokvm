"""Tests for the nanokvm command-line interface."""

from __future__ import annotations

from collections.abc import Iterator
import getpass
from typing import Any

import pytest

from nanokvm import cli
from nanokvm.client import NanoKVMApiError
from nanokvm.models import (
    GetGpioRsp,
    GetHardwareRsp,
    GetImagesRsp,
    GetInfoRsp,
    GetMountedImageRsp,
    GpioType,
    HWVersion,
)

_URL = "http://kvm.example.test/api/"


class _FakeKVM:
    """Stands in for NanoKVMSyncClient and records what the command does."""

    instances: list[_FakeKVM] = []
    # Power LED readings in order; the last one repeats.
    power_leds: list[bool] = [False]
    login_error: Exception | None = None

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

    def get_images(self) -> GetImagesRsp:
        return GetImagesRsp(files=["/data/zenos.img", "/data/other.iso"])

    def get_mounted_image(self) -> GetMountedImageRsp:
        return GetMountedImageRsp(file="/data/zenos.img")

    def mount_image(self, file: str | None, cdrom: bool = False) -> None:
        self.calls.append(("mount_image", file, cdrom))


@pytest.fixture(autouse=True)
def fake_kvm(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the client and the device settings for every test."""
    _FakeKVM.instances = []
    monkeypatch.setattr(_FakeKVM, "power_leds", [False])
    monkeypatch.setattr(_FakeKVM, "login_error", None)
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


def test_images_marks_the_mounted_one(capsys: pytest.CaptureFixture[str]) -> None:
    """The mounted image is starred."""
    assert cli.main(["images"]) == 0

    assert capsys.readouterr().out == "* /data/zenos.img\n  /data/other.iso\n"


@pytest.mark.parametrize(
    ("argv", "call"),
    [
        (["mount", "/data/zenos.img"], ("mount_image", "/data/zenos.img", False)),
        (["mount", "/data/x.iso", "--cdrom"], ("mount_image", "/data/x.iso", True)),
        (["unmount"], ("mount_image", None, False)),
    ],
)
def test_mount_and_unmount(argv: list[str], call: tuple[Any, ...]) -> None:
    """Mounting passes the image and the CD-ROM flag; unmounting passes None."""
    assert cli.main(argv) == 0

    assert call in _kvm().calls


def test_device_errors_exit_with_a_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Errors print one line instead of a traceback."""
    monkeypatch.setattr(
        _FakeKVM,
        "login_error",
        NanoKVMApiError("API returned error (Code: -2)", code=-2, msg="failed"),
    )

    assert cli.main(["status"]) == 1

    assert capsys.readouterr().err == (
        "nanokvm: error: API returned error (Code: -2)\n"
    )
