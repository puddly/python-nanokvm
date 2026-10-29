"""SSH client for NanoKVM terminal access."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from os import PathLike
import threading
from types import TracebackType

import paramiko

from .exceptions import NanoKVMError, NanoKVMNotSupportedError

DEFAULT_SSH_USERNAME = "root"

# The non-Pro kvm_system reboots the device when kvm_vision stops responding,
# but only while this file exists. The Pro firmware has no such watchdog.
_WATCHDOG_FILE = "/etc/kvm/watchdog"
_WATCHDOG_SUPPORTED = f"grep -qs {_WATCHDOG_FILE} /kvmapp/kvm_system/kvm_system"


class NanoKVMSSHError(NanoKVMError):
    """Base exception for SSH client errors."""


class NanoKVMSSHNotConnectedError(NanoKVMSSHError):
    """Exception for when SSH client is not connected."""


class NanoKVMSSHAuthenticationError(NanoKVMSSHError):
    """Exception for SSH authentication failures."""


class NanoKVMSSHConnectionError(NanoKVMSSHError):
    """Exception for SSH network or host-key failures."""


class NanoKVMSSHCommandError(NanoKVMSSHError):
    """Exception for SSH command execution errors."""


@dataclass(frozen=True, slots=True)
class SSHMetrics:
    """Device metrics read over SSH; a field is None when it can't be read."""

    boot_time: datetime | None = None
    cpu_temperature: float | None = None
    """Celsius."""
    memory_total: float | None = None
    """MiB."""
    memory_used_percent: float | None = None
    storage_total: float | None = None
    """MiB, of the root filesystem."""
    storage_used_percent: float | None = None
    watchdog_enabled: bool | None = None
    """None when the firmware has no watchdog (the Pro, or older non-Pro firmware)."""


@dataclass
class _CommandExecution:
    """Channel and cancellation state shared with one executor worker."""

    channel: paramiko.Channel | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        # Publish cancellation first so a channel opened after this check is
        # still closed by the worker, without affecting other executions.
        self.cancelled.set()
        channel = self.channel
        if channel is not None:
            channel.close()


class NanoKVMSSH:
    """SSH client for NanoKVM terminal access."""

    def __init__(
        self,
        host: str,
        username: str = DEFAULT_SSH_USERNAME,
        port: int = 22,
        *,
        known_hosts: str | PathLike[str] | None = None,
        allow_unknown_host_key: bool = False,
        connect_timeout: float | None = 10,
        banner_timeout: float | None = 10,
        auth_timeout: float | None = 10,
        allow_agent: bool = False,
        look_for_keys: bool = False,
    ) -> None:
        """Initialize the SSH client."""
        self.host = host
        self.port = port
        self.username = username
        self.known_hosts = known_hosts
        self.allow_unknown_host_key = allow_unknown_host_key
        self.connect_timeout = connect_timeout
        self.banner_timeout = banner_timeout
        self.auth_timeout = auth_timeout
        self.allow_agent = allow_agent
        self.look_for_keys = look_for_keys
        self.ssh_client: paramiko.SSHClient | None = None
        self._state_lock = asyncio.Lock()
        self._state_generation = 0

    async def __aenter__(self) -> NanoKVMSSH:
        """Enter an SSH client context without authenticating implicitly."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Disconnect when leaving an SSH client context."""
        await self.disconnect()

    async def authenticate(self, password: str) -> None:
        """Authenticate with SSH using password."""
        loop = asyncio.get_running_loop()
        client = paramiko.SSHClient()
        async with self._state_lock:
            self._state_generation += 1
            generation = self._state_generation

        def connect() -> None:
            # Host-key files are read here so the event loop never blocks on
            # file I/O.
            client.load_system_host_keys()
            if self.known_hosts is not None:
                client.load_host_keys(str(self.known_hosts))
            client.set_missing_host_key_policy(
                paramiko.AutoAddPolicy()
                if self.allow_unknown_host_key
                else paramiko.RejectPolicy()
            )
            client.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=password,
                timeout=self.connect_timeout,
                banner_timeout=self.banner_timeout,
                auth_timeout=self.auth_timeout,
                allow_agent=self.allow_agent,
                look_for_keys=self.look_for_keys,
            )

        try:
            connect_future = loop.run_in_executor(None, connect)
            try:
                await asyncio.shield(connect_future)
            except asyncio.CancelledError:
                # Cancellation does not stop a connect already running in the
                # executor. Close now and again when the worker completes so
                # a late successful connection cannot leak its transport.
                def close_after_connect(future: asyncio.Future[None]) -> None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        future.exception()
                    client.close()

                connect_future.add_done_callback(close_after_connect)
                client.close()
                raise
            async with self._state_lock:
                if generation != self._state_generation:
                    previous_client = None
                    superseded = True
                else:
                    previous_client = self.ssh_client
                    self.ssh_client = client
                    superseded = False
            if superseded:
                client.close()
                raise NanoKVMSSHNotConnectedError(
                    "SSH authentication was superseded by another session change"
                )
            if previous_client is not None:
                previous_client.close()
        except paramiko.AuthenticationException as e:
            client.close()
            raise NanoKVMSSHAuthenticationError(
                f"SSH authentication failed: {e}"
            ) from e
        except (paramiko.SSHException, paramiko.BadHostKeyException, OSError) as e:
            client.close()
            raise NanoKVMSSHConnectionError(f"SSH connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Close SSH connection."""
        async with self._state_lock:
            self._state_generation += 1
            client = self.ssh_client
            self.ssh_client = None
        if client is not None:
            client.close()

    async def run_command(self, command: str, timeout: float = 30) -> str:
        """Run a command via SSH and return output."""
        async with self._state_lock:
            client = self.ssh_client
        if client is None:
            raise NanoKVMSSHNotConnectedError(
                "SSH not connected, call authenticate first"
            )
        loop = asyncio.get_running_loop()
        execution = _CommandExecution()
        command_future = loop.run_in_executor(
            None, self._exec_command_sync, client, command, execution
        )
        try:
            output, error, exit_status = await asyncio.wait_for(
                command_future,
                timeout=timeout,
            )
            if exit_status != 0:
                detail = error.strip() or output.strip()
                suffix = f": {detail}" if detail else ""
                raise NanoKVMSSHCommandError(
                    f"SSH command exited with status {exit_status}{suffix}"
                )
            return output.strip()
        except TimeoutError:
            execution.cancel()
            raise NanoKVMSSHCommandError(
                f"SSH command timed out after {timeout} seconds"
            ) from None
        except asyncio.CancelledError:
            execution.cancel()
            raise
        except (paramiko.SSHException, OSError, EOFError) as e:
            self._invalidate_client(client)
            raise NanoKVMSSHCommandError(f"SSH command failed: {e}") from e

    async def get_metrics(self) -> SSHMetrics:
        """Read boot time, CPU temperature, memory, storage and watchdog state."""
        stat = await self.run_command("cat /proc/stat")
        temperature = await self.run_command(
            "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || true"
        )
        meminfo = await self.run_command("cat /proc/meminfo")
        df = await self.run_command("df -k /")
        memory_total, memory_used_percent = _parse_meminfo(meminfo)
        storage_total, storage_used_percent = _parse_df(df)
        return SSHMetrics(
            boot_time=_parse_boot_time(stat),
            cpu_temperature=_parse_temperature(temperature),
            memory_total=memory_total,
            memory_used_percent=memory_used_percent,
            storage_total=storage_total,
            storage_used_percent=storage_used_percent,
            watchdog_enabled=await self.get_watchdog_enabled(),
        )

    async def get_watchdog_enabled(self) -> bool | None:
        """Return the watchdog state, or None when the firmware has none."""
        # ponytail: greps the 380 KB kvm_system binary on every call (~0.3 s on
        # the PCIe); cache per connection if polling gets tight.
        output = await self.run_command(
            f"{_WATCHDOG_SUPPORTED} || {{ echo -; exit; }}; "
            f"test -f {_WATCHDOG_FILE} && echo 1 || echo 0"
        )
        return None if output == "-" else output == "1"

    async def set_watchdog_enabled(self, enabled: bool) -> None:
        """Enable or disable the watchdog.

        Raises ``NanoKVMNotSupportedError`` when the firmware has none.
        """
        change = f"touch {_WATCHDOG_FILE}" if enabled else f"rm -f {_WATCHDOG_FILE}"
        output = await self.run_command(
            f"{_WATCHDOG_SUPPORTED} || {{ echo -; exit; }}; {change}"
        )
        if output == "-":
            raise NanoKVMNotSupportedError("This NanoKVM firmware has no watchdog")

    def _invalidate_client(self, client: paramiko.SSHClient) -> None:
        """Drop a failed client unless a newer connection replaced it."""
        if self.ssh_client is client:
            self.ssh_client = None
        client.close()

    def _exec_command_sync(
        self,
        client: paramiko.SSHClient,
        command: str,
        execution: _CommandExecution,
    ) -> tuple[str, str, int]:
        """Synchronous SSH command execution."""
        if execution.cancelled.is_set():
            raise NanoKVMSSHCommandError("SSH command was cancelled")
        stdin, stdout, stderr = client.exec_command(command)
        del stdin
        channel = stdout.channel
        execution.channel = channel
        try:
            if execution.cancelled.is_set():
                raise NanoKVMSSHCommandError("SSH command was cancelled")
            with ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(stdout.read)
                stderr_future = executor.submit(stderr.read)
                output = stdout_future.result().decode("utf-8")
                error = stderr_future.result().decode("utf-8")
            return output, error, channel.recv_exit_status()
        finally:
            channel.close()


def _parse_boot_time(stat: str) -> datetime | None:
    for line in stat.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "btime" and parts[1].isdigit():
            return datetime.fromtimestamp(int(parts[1]), UTC)
    return None


def _parse_temperature(output: str) -> float | None:
    try:
        value = float(output)
    except ValueError:
        return None
    # Linux reports millidegrees; some kernels report degrees.
    if value > 1000:
        value /= 1000
    return round(value, 1)


def _parse_meminfo(meminfo: str) -> tuple[float | None, float | None]:
    values: dict[str, int] = {}
    for line in meminfo.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            values[parts[0].rstrip(":")] = int(parts[1])
    total_kb = values.get("MemTotal")
    if not total_kb:
        return None, None
    available_kb = values.get("MemAvailable")
    used_percent = (
        None
        if available_kb is None
        else round((total_kb - available_kb) / total_kb * 100, 2)
    )
    return round(total_kb / 1024, 2), used_percent


def _parse_df(df: str) -> tuple[float | None, float | None]:
    lines = df.splitlines()
    if len(lines) < 2:
        return None, None
    parts = lines[1].split()
    try:
        return round(int(parts[1]) / 1024, 2), float(parts[4].rstrip("%"))
    except (IndexError, ValueError):
        return None, None
