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
    images = await client.get_images()

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
or logging out also invalidates older authentication and password operations.
Superseded operations raise `NanoKVMNotAuthenticatedError`.

When an external `aiohttp.ClientSession` is supplied, it remains owned by the
caller. WebSockets retain its connector, headers, proxy cookies, basic auth,
proxy settings, and tracing, while isolating the device session cookie. Custom
session subclasses and middleware apply only to HTTP requests.

`NanoKVMApiError` includes the numeric API code. Its original `msg` and `data`
remain available as attributes but are omitted from automatic diagnostics.

## SSH

```python
from nanokvm.ssh_client import NanoKVMSSH

ssh = NanoKVMSSH("kvm.local")
await ssh.authenticate("password")

uptime = await ssh.run_command("cat /proc/uptime")
disk = await ssh.run_command("df -h /")

await ssh.disconnect()
```

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
