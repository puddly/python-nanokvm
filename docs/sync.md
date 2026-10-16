# Blocking client

`NanoKVMSyncClient` offers the API of `NanoKVMClient` as blocking calls, for
scripts that do not use asyncio:

```python
from pathlib import Path

from nanokvm.models import GpioType
from nanokvm.sync_client import NanoKVMSyncClient

with NanoKVMSyncClient("https://kvm.local/api/") as kvm:
    kvm.authenticate("username", "password")

    kvm.push_button(GpioType.POWER, duration_ms=6000)
    print(kvm.get_gpio().pwr)

    for frame in kvm.mjpeg_stream_bytes():
        Path("screen.jpg").write_bytes(frame)
        break
```

- Every coroutine method of `NanoKVMClient` is a blocking method with the same
  arguments, results and exceptions. Properties such as `token` and
  `hw_version` are available too.
- Methods that stream, such as `mjpeg_stream()` and `watch_image_download()`,
  return iterators. Leaving the loop early closes the stream.
- The constructor takes the arguments of `NanoKVMClient`, except `session`.
- The client runs on its own event loop in a background thread, so it also
  works where an event loop is already running, such as in Jupyter. Use it in a
  `with` block or call `close()`.
- Ctrl-C cancels the operation in progress.
- Progress callbacks run on the client's thread and cannot call the client.

Type checkers see the blocking methods as untyped. `run()` runs a coroutine of
the wrapped `client` on its event loop and keeps the result type:

```python
gpio = kvm.run(kvm.client.get_gpio())
```

`run()` is also the way to the async-only `open_ws()`, whose WebSocket belongs
to the client's event loop:

```python
async def read_message(client):
    ws = await client.open_ws("stream/h264/webrtc")
    try:
        return await ws.receive()
    finally:
        await ws.close()


message = kvm.run(read_message(kvm.client))
```
