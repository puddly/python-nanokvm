# python-nanokvm

Async Python client for [NanoKVM](https://github.com/sipeed/NanoKVM).

## Usage

```python

from aiohttp import ClientSession
from nanokvm.models import ButtonType
from nanokvm.client import NanoKVMClient


async with ClientSession() as session:
    client = NanoKVMClient("http://kvm-8b76.local/api/", session)
    await client.authenticate("username", "password")

    dev = await client.get_info()
    hw = await client.get_hardware()
    gpio = await client.get_gpio()

    await client.paste_text("Hello\nworld!")

    async for frame in client.mjpeg_stream():
        print(frame)

    await client.push_button(ButtonType.POWER, duration_ms=1000)
```