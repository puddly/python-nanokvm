"""SSH client for NanoKVM terminal access."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
from dataclasses import dataclass, field
from os import PathLike
import threading
from types import TracebackType

import paramiko

from .client import NanoKVMError

DEFAULT_SSH_USERNAME = "root"


class NanoKVMSSHError(NanoKVMError):
    """Base exception for SSH client errors."""


class NanoKVMSSHNotConnectedError(NanoKVMSSHError):
    """Exception for when SSH client is not connected."""


class NanoKVMSSHAuthenticationError(NanoKVMSSHError):
    """Exception for SSH authentication failures."""


class NanoKVMSSHCommandError(NanoKVMSSHError):
    """Exception for SSH command execution errors."""


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
    ) -> None:
        """Initialize the SSH client."""
        self.host = host
        self.port = port
        self.username = username
        self.known_hosts = known_hosts
        self.allow_unknown_host_key = allow_unknown_host_key
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

        try:
            client.load_system_host_keys()
            if self.known_hosts is not None:
                client.load_host_keys(str(self.known_hosts))
            client.set_missing_host_key_policy(
                paramiko.AutoAddPolicy()
                if self.allow_unknown_host_key
                else paramiko.RejectPolicy()
            )
            connect_future = loop.run_in_executor(
                None,
                lambda: client.connect(
                    self.host,
                    port=self.port,
                    username=self.username,
                    password=password,
                    timeout=10,
                    banner_timeout=10,
                    auth_timeout=10,
                ),
            )
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
            raise NanoKVMSSHAuthenticationError(f"SSH connection failed: {e}") from e

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
        except asyncio.TimeoutError:
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
