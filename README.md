# python-nanokvm

Async Python client for [NanoKVM](https://github.com/sipeed/NanoKVM).

## Usage

```python
from nanokvm.client import NanoKVMClient
from nanokvm.models import GpioType, MouseButton

#NanoKVM with HTTPS (recommended)
async with NanoKVMClient(
    "https://kvm.local/api/",
    use_password_obfuscation=False  # Newer versions use plain text
) as client:
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

### For Legacy NanoKVM (HTTP with password obfuscation)

```python
# Older NanoKVM versions with HTTP (backward compatibility)
async with NanoKVMClient(
    "http://kvm.local/api/",
    use_password_obfuscation=True  # Default: True for backward compatibility
) as client:
    await client.authenticate("username", "password")
```

**Note:** Password obfuscation is enabled by default for backward compatibility with older NanoKVM versions. For newer HTTPS-enabled devices, set `use_password_obfuscation=False`.

## HTTPS/SSL Configuration

The client supports HTTPS connections with flexible SSL/TLS configuration options.

### Standard HTTPS (Let's Encrypt, Public CA)

For modern NanoKVM devices with HTTPS and valid certificates:

```python
async with NanoKVMClient(
    "https://kvm.local/api/",
    use_password_obfuscation=False 
) as client:
    await client.authenticate("username", "password")
```

### Self-Signed Certificates

For self-signed certificates, you have two options:

#### Option 1: Disable verification (testing only)

**Warning:** This is insecure and should only be used for testing!

```python
async with NanoKVMClient(
    "https://kvm.local/api/",
    verify_ssl=False,
    use_password_obfuscation=False
) as client:
    await client.authenticate("username", "password")
```

#### Option 2: Use custom CA certificate (recommended)

```python
async with NanoKVMClient(
    "https://kvm.local/api/",
    ssl_ca_cert="/path/to/ca.pem",
    use_password_obfuscation=False
) as client:
    await client.authenticate("username", "password")
```
