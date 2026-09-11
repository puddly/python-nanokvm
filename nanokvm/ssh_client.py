"""SSH client for NanoKVM terminal access."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from os import PathLike

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
        self._active_channel: paramiko.Channel | None = None

    async def authenticate(self, password: str) -> None:
        """Authenticate with SSH using password."""
        loop = asyncio.get_running_loop()
        client = paramiko.SSHClient()

        try:
            client.load_system_host_keys()
            if self.known_hosts is not None:
                client.load_host_keys(str(self.known_hosts))
            client.set_missing_host_key_policy(
                paramiko.AutoAddPolicy()
                if self.allow_unknown_host_key
                else paramiko.RejectPolicy()
            )
            await loop.run_in_executor(
                None,
                lambda: client.connect(
                    self.host,
                    port=self.port,
                    username=self.username,
                    password=password,
                    timeout=10,
                ),
            )
            self.ssh_client = client
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
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None

    async def run_command(self, command: str, timeout: float = 30) -> str:
        """Run a command via SSH and return output."""
        if not self.ssh_client:
            raise NanoKVMSSHNotConnectedError(
                "SSH not connected, call authenticate first"
            )
        loop = asyncio.get_running_loop()
        command_future = loop.run_in_executor(None, self._exec_command_sync, command)
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
            if self._active_channel is not None:
                self._active_channel.close()
            raise NanoKVMSSHCommandError(
                f"SSH command timed out after {timeout} seconds"
            ) from None

    def _exec_command_sync(self, command: str) -> tuple[str, str, int]:
        """Synchronous SSH command execution."""
        assert self.ssh_client is not None  # Should be set after authenticate()
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        del stdin
        channel = stdout.channel
        self._active_channel = channel
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(stdout.read)
                stderr_future = executor.submit(stderr.read)
                output = stdout_future.result().decode("utf-8")
                error = stderr_future.result().decode("utf-8")
            return output, error, channel.recv_exit_status()
        finally:
            self._active_channel = None
