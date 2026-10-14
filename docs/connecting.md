# Connecting and authentication

The client URL points at the device's `/api/` path, such as
`https://kvm.local/api/`. Mouse coordinates are fractions of the screen, from
`0.0` to `1.0`.

## HTTPS

Certificates from a public or locally trusted CA work as is. For the device's
self-signed certificate, pin its fingerprint:

```python
from nanokvm.utils import async_fetch_remote_fingerprint

fingerprint = await async_fetch_remote_fingerprint("https://kvm.local/api/")
client = NanoKVMClient("https://kvm.local/api/", ssl_fingerprint=fingerprint)
```

Or trust a custom CA with `ssl_ca_cert="/path/to/ca.pem"`. `verify_ssl=False`
turns verification off; it does not check the device identity, so use it only
for testing.

## Sessions

- **Stored token:** pass `token=` to reuse a session instead of logging in.
  `client.token` holds the current one.
- **Your own `aiohttp.ClientSession`:** pass `session=`. It stays yours to close.
  WebSockets reuse its connector, headers, proxy settings, cookies, basic auth
  and tracing, while the device session cookie stays isolated. Session
  subclasses and middleware apply to HTTP requests only.
- **Timeout:** `request_timeout` (seconds, default 10) applies to each request.

## Authentication and accounts

`authenticate()` tries the obfuscated password format first and falls back to
plain text only after the device rejects the credentials. Pass
`use_password_obfuscation=True` or `False` to force one.

`get_account()` returns the username and, when the firmware provides it, the
role (older firmware returns `None`).

Starting another login or logging out invalidates operations and streams still
running on the old session; they raise `NanoKVMNotAuthenticatedError`. On
current non-Pro firmware, logging out revokes **every** session on the device,
including open web UI tabs.

### Changing the password

```python
await client.change_password(
    "username",
    "new-password",
    current_password="current-password",
)
```

Non-Pro firmware 2.5.1 and newer requires `current_password`, and the username
must match the logged-in account. Older non-Pro firmware and the Pro take only
the username and new password. A successful change clears the local session.

### User accounts

Non-Pro firmware 2.5.1 and newer has user accounts with the roles `admin` and
`user`. An admin can manage them:

```python
from nanokvm.models import UserRole

await client.create_user("guest", "password", UserRole.USER)
users = await client.get_users()
await client.update_user("guest", enabled=False)
await client.set_user_password("guest", "new-password")
await client.delete_user("guest")
```

Updating, deleting or resetting the password of a user ends that user's
sessions. The device owner cannot be disabled, demoted or deleted.
