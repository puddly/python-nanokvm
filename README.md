# python-nanokvm

Async Python client for [NanoKVM](https://github.com/sipeed/NanoKVM).

## Usage

```python

from aiohttp import ClientSession
from nanokvm import NanoKVMClient


async with ClientSession() as session:
    client = NanoKVMClient("http://kvm-8b76.local/api/", session)
    await client.authenticate("username", "password")

    hw_info = await client.get_hardware_info()
    dev_info = await client.get_device_info()
    gpio_state = await client.get_gpio_state()

    await client.send_keys("Hello\nworld!")

    async for frame in client.mjpeg_stream():
        print(frame)
```