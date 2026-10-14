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
