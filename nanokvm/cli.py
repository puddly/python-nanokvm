"""Command-line interface for one-off NanoKVM actions."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import getpass
import os
import sys
import time
from typing import Any

import aiohttp

from .exceptions import NanoKVMError
from .models import GpioType
from .sync_client import NanoKVMSyncClient

# Holding the power button this long forces a hung target off.
_FORCE_OFF_MS = 6000
_PRESS_MS = 800
_POLL_INTERVAL = 1.0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``nanokvm`` command and return its exit status."""
    parser = _parser()
    args = parser.parse_args(argv)
    url = args.url or os.environ.get("NANOKVM_URL")
    if not url:
        parser.error("the device URL is required: pass --url or set NANOKVM_URL")
    username = args.username or os.environ.get("NANOKVM_USERNAME") or "admin"
    password = os.environ.get("NANOKVM_PASSWORD")
    if password is None:
        password = getpass.getpass(f"Password for {username}: ")

    try:
        with NanoKVMSyncClient(
            url, verify_ssl=not args.insecure, ssl_ca_cert=args.ca_cert
        ) as kvm:
            kvm.authenticate(username, password)
            command: Callable[[Any, argparse.Namespace], int] = args.command
            return command(kvm, args)
    except (NanoKVMError, aiohttp.ClientError, TimeoutError, OSError) as err:
        print(f"nanokvm: error: {str(err) or type(err).__name__}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nanokvm",
        description="Run one-off actions on a NanoKVM. The password is read "
        "from NANOKVM_PASSWORD or asked for.",
    )
    parser.add_argument(
        "--url", help="API base, e.g. http://kvm.local/api/ (default: NANOKVM_URL)"
    )
    parser.add_argument(
        "--username", help="login name (default: NANOKVM_USERNAME, else admin)"
    )
    parser.add_argument("--ca-cert", help="CA certificate to trust for HTTPS")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="do not verify the HTTPS certificate",
    )
    commands = parser.add_subparsers(metavar="COMMAND", required=True)

    commands.add_parser(
        "info", help="show the hardware and firmware versions"
    ).set_defaults(command=_info)
    commands.add_parser(
        "status", help="show the target's power and HDD LEDs"
    ).set_defaults(command=_status)

    power = commands.add_parser(
        "power", help="press the target's power button"
    ).add_subparsers(metavar="ACTION", required=True)
    press = power.add_parser("press", help="press the power button")
    press.add_argument("--ms", type=int, default=_PRESS_MS, help="press duration")
    press.set_defaults(command=_power_press)
    for action, command, help_text in (
        ("on", _power_on, "turn the target on with a short press"),
        ("off", _power_off, "force the target off by holding the button"),
    ):
        subcommand = power.add_parser(action, help=help_text)
        subcommand.add_argument(
            "--timeout",
            type=float,
            default=30.0,
            help="seconds to wait for the power LED (default: 30)",
        )
        subcommand.set_defaults(command=command)

    reset = commands.add_parser("reset", help="press the target's reset button")
    reset.add_argument("--ms", type=int, default=_PRESS_MS, help="press duration")
    reset.set_defaults(command=_reset)

    commands.add_parser(
        "images", help="list the images; * marks the mounted one"
    ).set_defaults(command=_images)
    mount = commands.add_parser("mount", help="mount an image on the target")
    mount.add_argument("image", help="image path, as listed by 'images'")
    mount.add_argument("--cdrom", action="store_true", help="mount as a CD-ROM")
    mount.set_defaults(command=_mount)
    commands.add_parser("unmount", help="unmount the image").set_defaults(
        command=_unmount
    )
    return parser


def _info(kvm: Any, args: argparse.Namespace) -> int:
    hardware = kvm.get_hardware()
    info = kvm.get_info()
    print(f"hardware: {hardware.version}")
    print(f"application: {info.application}")
    print(f"image: {info.image}")
    return 0


def _status(kvm: Any, args: argparse.Namespace) -> int:
    gpio = kvm.get_gpio()
    print(f"power LED: {_on_off(gpio.pwr)}")
    print(f"HDD LED: {_on_off(gpio.hdd)}")
    return 0


def _power_press(kvm: Any, args: argparse.Namespace) -> int:
    kvm.push_button(GpioType.POWER, args.ms)
    return 0


def _power_on(kvm: Any, args: argparse.Namespace) -> int:
    return _switch_power(kvm, on=True, press_ms=_PRESS_MS, timeout=args.timeout)


def _power_off(kvm: Any, args: argparse.Namespace) -> int:
    return _switch_power(kvm, on=False, press_ms=_FORCE_OFF_MS, timeout=args.timeout)


def _switch_power(kvm: Any, *, on: bool, press_ms: int, timeout: float) -> int:
    """Press power unless the LED already shows ``on``, then wait for it."""
    state = _on_off(on)
    if kvm.get_gpio().pwr == on:
        print(f"already {state}")
        return 0
    kvm.push_button(GpioType.POWER, press_ms)
    deadline = time.monotonic() + timeout
    while kvm.get_gpio().pwr != on:
        if time.monotonic() >= deadline:
            print(
                f"nanokvm: error: the power LED is not {state} after {timeout:g} s",
                file=sys.stderr,
            )
            return 1
        time.sleep(_POLL_INTERVAL)
    print(state)
    return 0


def _reset(kvm: Any, args: argparse.Namespace) -> int:
    kvm.push_button(GpioType.RESET, args.ms)
    return 0


def _images(kvm: Any, args: argparse.Namespace) -> int:
    mounted = kvm.get_mounted_image().file
    for image in kvm.get_images().files:
        print(f"{'*' if image == mounted else ' '} {image}")
    return 0


def _mount(kvm: Any, args: argparse.Namespace) -> int:
    kvm.mount_image(args.image, cdrom=args.cdrom)
    return 0


def _unmount(kvm: Any, args: argparse.Namespace) -> int:
    kvm.mount_image(None)
    return 0


def _on_off(value: bool) -> str:
    return "on" if value else "off"
