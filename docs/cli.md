# Command line

Installing the package adds a `nanokvm` command for one-off actions, built on
the [blocking client](sync.md):

```console
$ export NANOKVM_URL=http://kvm.local/api/
$ nanokvm info
Password for admin:
hardware: PCIE
application: 2.5.2
image: v1.4.2
$ nanokvm power off
off
$ nanokvm media
* debian.iso
  (data partition)
$ nanokvm unmount
```

| Command | Action |
|---|---|
| `info` | Hardware, application and image versions |
| `status` | Power and HDD LEDs |
| `power press [--ms 800]` | Press the power button |
| `power on [--timeout 30]` | Short press, unless the power LED is already on; wait for the LED |
| `power off [--timeout 30]` | Hold the power button for 6 s to force the target off, unless the LED is already off; wait for the LED |
| `reset [--ms 800]` | Press the reset button |
| `media` | List what can be mounted; `*` marks the mounted one |
| `mount [IMAGE] [--cdrom] [--force]` | Mount an image on the target as a USB disk or a CD-ROM |
| `unmount [--force]` | Unmount the mounted medium |

- `--url` or `NANOKVM_URL` selects the device, and `--username` or
  `NANOKVM_USERNAME` the account (default `admin`).
- The password comes from `NANOKVM_PASSWORD` or is asked for. There is no
  option for it, so it never shows in the process list.
- For HTTPS, `--ca-cert PATH` trusts a custom CA and `--insecure` skips
  certificate verification (see [Connecting](connecting.md)).
- The exit status is 0 on success, 1 when the device reports an error or
  cannot be reached (with a one-line message that includes the device's
  reason), and 2 for usage errors.

## Media

`mount` takes an image as `media` lists it. From non-Pro 2.5.3, that is its
path in the media directory, such as `debian.iso`. On older firmware and on the
Pro, it is the full path, such as `/data/debian.iso`.

From non-Pro 2.5.3:

- `media` also lists the device's shared data partition, and `mount` without
  an image attaches it.
- `unmount` leaves the target without a disk.
- While the target uses the disk, the device refuses to mount or unmount.
  `--force` takes the disk back, losing the target's unflushed writes.

On older non-Pro firmware, `unmount` gives the target the data partition
instead. The API reference has the details for
[images](api.md#images-and-scripts) and for
[virtual media](api.md#virtual-media).
