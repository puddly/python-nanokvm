# API reference

## Hardware and firmware support

There are two hardware families:

- **non-Pro:** the original NanoKVM (hardware Alpha and Beta) and the NanoKVM PCIe
- **Pro:** NanoKVM Pro

Many endpoints exist on only one family or need a minimum application version.
Each method declares what it needs. The client checks it and raises
`NanoKVMNotSupportedError` without contacting the device:

```python
from nanokvm.client import NanoKVMNotSupportedError

try:
    await client.get_swap_size()
except NanoKVMNotSupportedError as error:
    print(error)  # get_swap_size requires hardware: non-Pro (detected: Pro)
```

The hardware is detected at login, or on the first call when the client
starts from a stored `token`. Firmware versions are detected on the first call
that needs them, and kept until the session resets. After updating the firmware
on a client that stays logged in, call `await client.detect_versions()`.
Version strings that are not plain numbers, such as development builds, count
as supported.

`client.hw_version`, `client.application_version` and `client.image_version`
expose what was detected.

## Methods

Every method below is a coroutine on `NanoKVMClient`. The requirements column
uses these abbreviations:

- **all:** every device
- **non-Pro 2.3.1:** non-Pro with application 2.3.1 or newer, and not the Pro
- **non-Pro 2.2.6 / Pro:** non-Pro 2.2.6 or newer, or any Pro
- **PCIe:** NanoKVM PCIe only

Request and response models live in `nanokvm.models`.

### Device and system

| Methods | Requires |
|---|---|
| `get_info`, `get_hardware`, `get_gpio`, `push_button` | all |
| `get_oled_info`, `set_oled_sleep` | all |
| `get_virtual_device_status`, `update_virtual_device` | all |
| `get_hostname`, `set_hostname`, `get_web_title`, `set_web_title` | non-Pro 2.2.6 / Pro |
| `get_ssh_state`, `enable_ssh`, `disable_ssh` | non-Pro 2.1.6 / Pro |
| `get_mdns_state`, `enable_mdns`, `disable_mdns` | non-Pro 2.2.2 / Pro |
| `reboot_system` | non-Pro 2.2.2 / Pro |
| `set_tls` | non-Pro 2.2.7 |
| `get_swap_size`, `set_swap_size` | non-Pro 2.2.6 |
| `get_memory_limit`, `set_memory_limit` | non-Pro |
| `switch_to_pikvm`, `get_led_strip`, `set_led_strip` | Pro |
| `get_lcd_time_format`, `set_lcd_time_format` | Pro 1.1.6 |
| `get_timezone`, `set_timezone`, `get_time_status`, `sync_time` | Pro 1.1.6 |
| `get_time` | Pro 1.2.6 |
| `get_low_power`, `set_low_power`, `refresh_virtual_device` | Pro 1.2.7 |
| `get_menubar_config`, `set_menubar_config` | Pro 1.2.10 |

`set_tls` restarts the device's web server, which then answers only on the
other scheme (`http` or `https`), within about half a minute.

### Keyboard and mouse

| Methods | Requires |
|---|---|
| `paste_text` | all |
| `key_down`, `key_up`, `key_tap` | all, see below |
| `mouse_move_abs`, `mouse_move_rel`, `mouse_down`, `mouse_up`, `mouse_click`, `mouse_scroll` | all |
| `get_hid_mode`, `set_hid_mode` | non-Pro 2.2.5 / Pro |
| `reset_hid` | non-Pro / Pro 1.1.6 |
| `get_mouse_jiggler_state`, `set_mouse_jiggler_state` | non-Pro 2.2.6 / Pro |
| `get_shortcuts`, `add_shortcut`, `delete_shortcut` | non-Pro 2.3.2 / Pro 1.2.8 |
| `get_leader_key`, `set_leader_key` | non-Pro 2.3.4 / Pro 1.2.12 |
| `get_keyboard_led_status` | non-Pro 2.5.0 |

`paste_text` accepts only the characters in `nanokvm.client.PASTE_CHAR_MAP`.

The key methods send USB HID key codes, such as `KeyCode.F12` or any `int`,
with modifiers such as `KeyModifier.LEFT_CTRL`:

```python
from nanokvm.models import KeyCode, KeyModifier

await client.key_tap(
    KeyCode.DELETE, modifiers=KeyModifier.LEFT_CTRL | KeyModifier.LEFT_ALT
)
```

- `key_down` holds up to six keys and replaces the keys held before. `key_up`
  releases them all. `key_tap` presses and releases, and still releases when
  it is cancelled.
- Before non-Pro 2.3.2 and Pro 1.2.6, the device holds one key at a time and
  applies modifiers only together with a key. Other reports raise
  `NanoKVMNotSupportedError`.
- The keyboard and mouse share one WebSocket. While it is open, the client
  sends a heartbeat every 10 seconds, as the web UI does, so that non-Pro 2.5.0
  and newer do not close it when idle.

To catch a short boot-menu window, tap a key repeatedly:

```python
loop = asyncio.get_running_loop()
end = loop.time() + 8
while loop.time() < end:
    await client.key_tap(KeyCode.F12)
    await asyncio.sleep(0.4)
```

### Video and streaming

| Methods | Requires |
|---|---|
| `mjpeg_stream`, `mjpeg_stream_bytes`, `open_ws`, `open_webrtc_signaling` | all |
| `get_snapshot`, `set_screen`, `set_frame_detect`, `pause_frame_detect` | non-Pro |
| `get_input_region`, `set_input_region`, `get_input_resolution` | non-Pro 2.5.1 |
| `reset_hdmi` | PCIe 2.1.5 |
| `get_hdmi_state`, `enable_hdmi`, `disable_hdmi` | PCIe 2.2.8 |
| `set_hdmi_idle_timeout` | PCIe 2.5.0 |
| `set_stream_mode`, `set_stream_quality`, `set_gop` | Pro |
| `get_hdmi_capture`, `set_hdmi_capture`, `get_hdmi_passthrough`, `set_hdmi_passthrough` | Pro |
| `get_edid`, `switch_edid` | Pro |
| `get_custom_edid_list`, `upload_edid`, `delete_edid` | Pro 1.2.3 |
| `set_rate_control_mode` | Pro 1.2.6 |
| `set_fps` | Pro 1.2.8 |

`mjpeg_stream()` yields decoded `PIL.Image` frames; `mjpeg_stream_bytes()`
yields the raw JPEG bytes. `get_snapshot(timeout=20.0)` returns the first raw
JPEG frame; the Pro refuses it, because opening MJPEG there interrupts WebRTC.
`open_ws(path)` opens a new WebSocket on a path relative to the API base, such
as `stream/h264/webrtc`, for protocols the client does not wrap. The caller closes it before the client exits.

`open_webrtc_signaling()` opens the H.264 WebRTC signaling session for the
detected hardware and returns a `nanokvm.webrtc.WebRTCSignaling`. Send the
browser's offer with `send_offer(sdp)` and its candidates with
`send_candidate(IceCandidate(...))`, and iterate it for `WebRTCAnswer` and
`WebRTCCandidate` events. The Pro answers audio and video on separate peer
connections; the session splits the offer and merges the answers, so the
caller sees one offer and one answer on any hardware. Iteration raises
`NanoKVMVideoModeError` when the Pro drops WebRTC because another video mode
took the encoder, for example an MJPEG stream. Close it, or use it as an
async context manager, before the client exits.

```python
async with await client.open_webrtc_signaling() as signaling:
    await signaling.send_offer(offer_sdp)
    async for event in signaling:
        ...  # forward WebRTCAnswer.sdp and WebRTCCandidate.candidate
```

### Images and scripts

| Methods | Requires |
|---|---|
| `get_images`, `get_mounted_image`, `mount_image` | all |
| `upload_image` | non-Pro 2.3.1 / Pro, see below |
| `delete_image` | non-Pro 2.3.0 / Pro 1.1.6 |
| `download_image`, `get_image_download_status`, `is_image_download_enabled`, `watch_image_download` | non-Pro 2.1.6 / Pro |
| `cancel_image_download` | non-Pro 2.5.0 |
| `get_cdrom_status` | non-Pro 2.2.1 |
| `get_scripts`, `upload_script`, `run_script`, `delete_script` | all |
| `get_autostart_scripts`, `get_autostart_script`, `upload_autostart_script`, `delete_autostart_script` | non-Pro 2.3.1 |

Mount an image on the host as a USB disk, or as a CD-ROM with `cdrom=True`:

```python
images = await client.get_images()  # images.files: ["/data/debian.iso", ...]
await client.mount_image("/data/debian.iso", cdrom=True)
mounted = await client.get_mounted_image()  # mounted.file: "/data/debian.iso"
await client.mount_image()  # unmount
```

- `get_images()` lists the `.iso` and `.img` files on the device by the full
  path that `mount_image()` takes. `get_mounted_image().file` is empty when no
  image is mounted.
- `mount_image()` without a file unmounts. On non-Pro devices, the host then
  sees the device's own `/data` partition as the USB disk. To remove the disk,
  `update_virtual_device(VirtualDevice.DISK)` toggles it, and
  `get_virtual_device_status().disk` reports whether it is on.
- `read_only=True` needs Pro 1.2.1 or newer.
- Mounting and unmounting reconnect the host's USB devices, including the
  keyboard and mouse. The Pro answers about 3 seconds later.
- Non-Pro firmware before 2.5.2 keeps CD-ROM mode when a disk image is mounted
  right after a CD-ROM image. Unmount first. From 2.5.2, the host can only
  read a mounted image.

Upload an image with progress reporting, then mount it:

```python
def on_progress(progress):
    print(f"{progress.percentage:.0f}%")

await client.upload_image("debian.iso", progress_callback=on_progress)
await client.mount_image("/data/debian.iso", cdrom=True)
```

- Non-Pro devices need application 2.3.1 or newer and accept `.iso` files with
  ASCII names. On 2.5.0 or newer, pass `sha256=` to have the device verify the
  upload.
- The Pro accepts `.iso` and `.img` files.
- An existing image with the same name raises `FileExistsError` unless
  `overwrite=True`. On the Pro, overwriting deletes the old image before the
  upload starts, so a failed upload leaves neither copy.

Or have the device download the image itself (`sha256=` needs non-Pro 2.5.0):

```python
await client.download_image("https://example.com/debian.iso")
async for status in client.watch_image_download():
    print(status.status, status.percentage)
```

Autostart scripts run when the device boots.

### Network

| Methods | Requires |
|---|---|
| `get_wifi_status`, `connect_wifi_no_auth` | all |
| `send_wake_on_lan`, `get_wol_macs`, `delete_wol_mac` | all |
| `set_wol_mac_name` | non-Pro 2.2.6 / Pro |
| `connect_wifi`, `disconnect_wifi` | non-Pro 2.3.1 / Pro |
| `verify_ap_login` | non-Pro 2.3.6 / Pro 1.2.14 |
| `get_dns`, `set_dns` | non-Pro 2.4.1 |
| `scan_wifi` | Pro 1.1.7 |
| `get_static_ip`, `set_static_ip` | Pro 1.2.3 |

### Updates and extensions

| Methods | Requires |
|---|---|
| `get_application_version`, `update_application`, `install_application_update` | all, see below |
| `get_preview_status`, `set_preview_state` | non-Pro 2.2.5 / Pro |
| `update_application_offline` | non-Pro 2.3.1 |
| `get_update_server`, `set_update_server` | non-Pro 2.5.1 |
| `get_tailscale_status`, `tailscale_install`, `tailscale_uninstall`, `tailscale_up`, `tailscale_down`, `tailscale_login`, `tailscale_logout`, `tailscale_start`, `tailscale_stop`, `tailscale_restart` | non-Pro 2.1.6 / Pro |
| `get_mcp_config`, `set_mcp_enabled`, `regenerate_mcp_api_key` | non-Pro 2.5.0 |
| `get_ai_control_status`, `set_ai_control_mode` | non-Pro 2.5.0 |
| `assistant_install`, `assistant_start` | Pro 1.1.4 |
| `kvmadmin_install`, `kvmadmin_uninstall`, `kvmadmin_start`, `kvmadmin_stop`, `kvmadmin_status` | Pro 1.1.5 |

`install_application_update()` updates the application when the device is
out of date, and waits until it runs the new version, which it returns. The
update restarts the device's server and ends the session, so the client logs
in again with credentials kept by `keep_credentials=True` (see
[Connecting](connecting.md#keeping-credentials)):

```python
async with NanoKVMClient(url, keep_credentials=True) as client:
    await client.authenticate("username", "password")
    version = await client.install_application_update()
```

- Without kept credentials it raises `NanoKVMNotAuthenticatedError` before
  contacting the device. It raises `NanoKVMError` when the device cannot reach
  its update server, and `TimeoutError` when the new version is not running
  within `timeout` (default 10 minutes).
- `get_application_version()` returns the `current` and `latest` versions.
  `latest` is empty when the device cannot reach its update server. The device
  asks the server while answering, so the call waits up to 60 seconds.
- `update_application()` answers once the update is installed, which can take
  minutes; it waits up to 15 minutes. It does not wait for the restart. Non-Pro
  firmware before 2.5.1 restarts before it answers, so the call returns when
  the connection drops, without confirming the update; the same applies to
  `update_application_offline()`. `install_application_update()` confirms it.
- With `set_preview_state(True)`, the device installs preview releases.

`update_application_offline("nanokvm_2.5.1.tar.gz")` installs an official
package from a local file. Pass `sha256=` to check the file first; non-Pro
2.5.1 and newer also check it on the device.

### Accounts

| Methods | Requires |
|---|---|
| `authenticate`, `logout`, `get_account`, `change_password`, `is_password_updated` | all |
| `get_users`, `create_user`, `update_user`, `delete_user`, `set_user_password` | non-Pro 2.5.1 |
