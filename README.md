# python-nanokvm

Async Python client for [NanoKVM](https://github.com/sipeed/NanoKVM).

## Usage

```python
from nanokvm.client import NanoKVMClient
from nanokvm.models import GpioType, MouseButton

async with NanoKVMClient("http://kvm-8b76.local/api/") as client:
    await client.authenticate("username", "password")

    # Get device information
    dev = await client.get_info()
    hw = await client.get_hardware()
    gpio = await client.get_gpio()

    # List available images
    images = await client.get_images()

    # Keyboard input
    await client.paste_text("Hello\nworld!")

    # Mouse control
    await client.mouse_click(MouseButton.LEFT, 0.5, 0.5)
    await client.mouse_move_abs(0.25, 0.75)
    await client.mouse_scroll(0, -3)

    # Stream video
    async for frame in client.mjpeg_stream():
        print(frame)

    # Control GPIO
    await client.push_button(GpioType.POWER, duration_ms=1000)
```

## SSH Usage

```python
from nanokvm.ssh_client import NanoKVMSSH

# Create SSH client
ssh = NanoKVMSSH("kvm-8b76.local")
await ssh.authenticate("password")

# Run commands
uptime = await ssh.run_command("cat /proc/uptime")
disk = await ssh.run_command("df -h /")

await ssh.disconnect()
```
