# SSH

`NanoKVMSSH` runs commands on the device over SSH:

```python
from pathlib import Path

from nanokvm.ssh_client import NanoKVMSSH

async with NanoKVMSSH(
    "kvm.local",
    known_hosts=Path.home() / ".ssh" / "known_hosts",
) as ssh:
    await ssh.authenticate("password")
    uptime = await ssh.run_command("cat /proc/uptime")
```

- Host keys are verified strictly: add the device key to the system
  known-hosts file or pass `known_hosts=`. `allow_unknown_host_key=True`
  disables the check; use it only for controlled testing.
- Password login does not use an SSH agent or look for private keys unless you
  pass `allow_agent=True` or `look_for_keys=True`.
- `connect_timeout`, `banner_timeout` and `auth_timeout` tune each connection
  phase.
- Failures raise `NanoKVMSSHAuthenticationError`, `NanoKVMSSHConnectionError`
  (including host-key errors) or `NanoKVMSSHCommandError`.

## Metrics and watchdog

```python
metrics = await ssh.get_metrics()
metrics.cpu_temperature  # 45.8 (°C)
```

`get_metrics()` returns an `SSHMetrics` with `boot_time` (UTC),
`cpu_temperature`, `memory_total` and `storage_total` (MiB, root filesystem),
`memory_used_percent` and `storage_used_percent`, and `watchdog_enabled`. A
field is `None` when the device output can't be parsed.

The watchdog is the non-Pro firmware's own: `kvm_system` reboots the device
when `kvm_vision` stops responding for more than 10 seconds, but only while
`/etc/kvm/watchdog` exists. `get_watchdog_enabled()` returns `None` when the
firmware has no watchdog (the Pro, or older non-Pro firmware), and
`set_watchdog_enabled()` raises `NanoKVMNotSupportedError` there.
