# python-nanokvm

Async Python client for the [Sipeed NanoKVM](https://github.com/sipeed/NanoKVM)
and [NanoKVM Pro](https://github.com/sipeed/NanoKVM-Pro).

```bash
pip install nanokvm
```

## Usage

```python
import asyncio

from nanokvm.client import NanoKVMClient
from nanokvm.models import GpioType, MouseButton


async def main() -> None:
    async with NanoKVMClient("https://kvm.local/api/") as client:
        await client.authenticate("username", "password")

        await client.push_button(GpioType.POWER, duration_ms=1000)
        await client.paste_text("Hello\nworld!")
        await client.mouse_click(MouseButton.LEFT, 0.5, 0.5)

        async for frame in client.mjpeg_stream():
            frame.save("screen.jpg")
            break


asyncio.run(main())
```

The client detects the hardware and firmware, and raises
`NanoKVMNotSupportedError` instead of calling an endpoint the device lacks.

Scripts that do not use asyncio can use the same API as blocking calls through
`nanokvm.sync_client.NanoKVMSyncClient`.

## Documentation

- [Connecting and authentication](docs/connecting.md): HTTPS and self-signed
  certificates, sessions, passwords and user accounts
- [API reference](docs/api.md): every method and the hardware and firmware it
  needs
- [Video streaming](docs/streaming.md): snapshots, MJPEG and WebRTC signaling
- [Blocking client](docs/sync.md): the API without asyncio
- [Command line](docs/cli.md): the `nanokvm` command for one-off actions
- [Errors](docs/errors.md)
- [SSH](docs/ssh.md)
