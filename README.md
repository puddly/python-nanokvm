# python-nanokvm

Async Python client for [NanoKVM](https://github.com/sipeed/NanoKVM).

## Basic usage

```python
from nanokvm.client import NanoKVMClient
from nanokvm.models import GpioType, MouseButton

async with NanoKVMClient("https://kvm.local/api/") as client:
    await client.authenticate("username", "password")

    info = await client.get_info()
    hardware = await client.get_hardware()
    gpio = await client.get_gpio()

    await client.paste_text("Hello\nworld!")
    await client.mouse_click(MouseButton.LEFT, 0.5, 0.5)
    await client.mouse_move_abs(0.25, 0.75)
    await client.mouse_scroll(0, -3)
    await client.push_button(GpioType.POWER, duration_ms=1000)

    async for frame in client.mjpeg_stream():
        print(frame)
```

## Authentication and permissions

Authentication tries the obfuscated password format first, then plain text only
after the credentials are rejected. Pass `use_password_obfuscation=False` or
`True` to force either mode.

`get_account()` returns the username and, when provided by the firmware, its
role. Older firmware may return `None` for `role`. Permission failures raise
`NanoKVMPermissionError` without ending the session:

```python
from nanokvm.client import NanoKVMPermissionError

account = await client.get_account()
try:
    await client.get_images()
except NanoKVMPermissionError as error:
    print(error.status, error.method, error.path)
```

On non-Pro firmware 2.5.1 and newer, changing a password requires the current
password and the username must match the authenticated account:

```python
await client.change_password(
    "username",
    "new-password",
    current_password="current-password",
)
```

Older non-Pro firmware and NanoKVM Pro use the original two-argument call.

A successful password change clears the local session. Starting another login
or logging out invalidates in-flight authenticated operations and streams.
Superseded operations raise `NanoKVMNotAuthenticatedError`.

When an external `aiohttp.ClientSession` is supplied, it remains owned by the
caller. WebSockets retain its connector, headers, proxy cookies, basic auth,
proxy settings, and tracing, while isolating the device session cookie. Custom
session subclasses and middleware apply only to HTTP requests.

`NanoKVMApiError` includes the numeric API code. Its original `msg` and `data`
remain available as attributes but are omitted from automatic diagnostics.

## Hardware and firmware support

Each method knows which hardware and application version it needs. The client
detects both and raises `NanoKVMNotSupportedError` before sending a request the
device cannot serve:

```python
from nanokvm.client import NanoKVMNotSupportedError

try:
    await client.get_swap_size()
except NanoKVMNotSupportedError as error:
    print(error)  # get_swap_size requires hardware: non-Pro (detected: Pro)
```

Hardware is detected at login, or on the first call when the client was created
from a stored `token`. Application and image versions are detected on the first
version-checked call and kept until the session resets (logout, a new login or
an expired session). After a firmware update on a client that stays logged in,
call `await client.detect_versions()` to refresh them. Version strings that are
not plain numbers, such as development builds, are treated as supported.

The HDMI controls (`get_hdmi_state`, `reset_hdmi`, `enable_hdmi`,
`disable_hdmi`, `set_hdmi_idle_timeout`) are available on PCIe hardware only.

## Images

`upload_image` sends a local image to the device and reports progress:

```python
def on_progress(progress):
    print(f"{progress.percentage:.0f}%")

await client.upload_image("debian.iso", progress_callback=on_progress)
await client.mount_image("/data/debian.iso", cdrom=True)
```

- Non-Pro devices need application 2.3.1 or newer and accept `.iso` files with
  ASCII names. Pass `sha256=` (2.5.0 or newer) to have the device verify the
  upload.
- NanoKVM Pro accepts `.iso` and `.img` files.
- An existing image with the same name raises `FileExistsError` unless
  `overwrite=True`. On NanoKVM Pro, overwriting deletes the existing image
  before the upload starts, so a failed upload leaves neither copy.

The device can also download an image itself:

```python
await client.download_image("https://example.com/debian.iso")
async for status in client.watch_image_download():
    print(status.status, status.percentage)
```

`download_image` accepts `sha256=` and `cancel_image_download()` stops a
download; both need a non-Pro device on 2.5.0 or newer.

## Other non-Pro features

- **Application updates:** `update_application_offline("nanokvm_2.5.1.tar.gz")`
  installs an official package from a local file (2.3.1 or newer);
  `get_update_server` and `set_update_server` manage a custom update server
  (2.5.1 or newer).
- **MCP and coordinated control** (2.5.0 or newer): `get_mcp_config`,
  `set_mcp_enabled`, `regenerate_mcp_api_key`, `get_ai_control_status` and
  `set_ai_control_mode`.
- **Input region** (2.5.1 or newer): `get_input_region`, `set_input_region` and
  `get_input_resolution`.

## SSH

SSH host-key verification is strict by default. Add the device key to the
system known-hosts file or pass a separate file with `known_hosts`. Password
authentication does not use an SSH agent or private-key discovery unless
`allow_agent=True` or `look_for_keys=True` is set.
Pass `connect_timeout`, `banner_timeout`, or `auth_timeout` to adjust the
connection phases.

```python
from pathlib import Path

from nanokvm.ssh_client import NanoKVMSSH

async with NanoKVMSSH(
    "kvm.local",
    known_hosts=Path.home() / ".ssh" / "known_hosts",
) as ssh:
    await ssh.authenticate("password")

    uptime = await ssh.run_command("cat /proc/uptime")
    disk = await ssh.run_command("df -h /")
```

Set `allow_unknown_host_key=True` only for controlled testing; it disables
host-key verification. Authentication failures raise
`NanoKVMSSHAuthenticationError`, connection and host-key failures raise
`NanoKVMSSHConnectionError`, and command failures raise
`NanoKVMSSHCommandError`.

## HTTPS

Certificates issued by a public or locally trusted CA work without extra
configuration:

```python
client = NanoKVMClient("https://kvm.local/api/")
```

For a self-signed certificate, certificate pinning is recommended:

```python
from nanokvm.utils import async_fetch_remote_fingerprint

fingerprint = await async_fetch_remote_fingerprint("https://kvm.local/api/")
client = NanoKVMClient(
    "https://kvm.local/api/",
    ssl_fingerprint=fingerprint,
)
```

A custom CA certificate can be supplied instead:

```python
client = NanoKVMClient("https://kvm.local/api/", ssl_ca_cert="/path/to/ca.pem")
```

Certificate verification can be disabled for testing, but this does not verify
the device identity and should not be used in production:

```python
client = NanoKVMClient("https://kvm.local/api/", verify_ssl=False)
```
