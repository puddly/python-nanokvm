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
$ nanokvm mount /data/disk.img
```

| Command | Action |
|---|---|
| `info` | Hardware, application and image versions |
| `status` | Power and HDD LEDs |
| `power press [--ms 800]` | Press the power button |
| `power on [--timeout 30]` | Short press, unless the power LED is already on; wait for the LED |
| `power off [--timeout 30]` | Hold the power button for 6 s to force the target off, unless the LED is already off; wait for the LED |
| `reset [--ms 800]` | Press the reset button |
| `images` | List the images; `*` marks the mounted one |
| `mount IMAGE [--cdrom]`, `unmount` | Mount an image on the target, or unmount it |

- `--url` or `NANOKVM_URL` selects the device, and `--username` or
  `NANOKVM_USERNAME` the account (default `admin`).
- The password comes from `NANOKVM_PASSWORD` or is asked for. There is no
  option for it, so it never shows in the process list.
- For HTTPS, `--ca-cert PATH` trusts a custom CA and `--insecure` skips
  certificate verification (see [Connecting](connecting.md)).
- The exit status is 0 on success, 1 when the device reports an error or
  cannot be reached (with a one-line message), and 2 for usage errors.
