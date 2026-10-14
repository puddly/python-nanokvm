# Errors

All exceptions derive from `NanoKVMError` and are importable from
`nanokvm.client`.

| Exception | Raised when |
|---|---|
| `NanoKVMNotSupportedError` | The detected hardware or firmware lacks the endpoint; nothing was sent |
| `NanoKVMAuthenticationFailure` | Login was rejected |
| `NanoKVMNotAuthenticatedError` | There is no session, or a newer login or logout replaced it |
| `NanoKVMPermissionError` | The account lacks permission; the session stays valid |
| `NanoKVMApiError` | The device answered with an error code |
| `NanoKVMInvalidResponseError` | The response could not be parsed |

`NanoKVMPermissionError` carries `status`, `method` and `path`.
`NanoKVMApiError` carries the numeric `code`; the device's `msg` and `data` are
available as attributes but left out of the exception message.
