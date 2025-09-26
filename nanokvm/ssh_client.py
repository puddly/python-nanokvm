"""SSH client for NanoKVM terminal access."""

from __future__ import annotations

import asyncio

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
        self, host: str, username: str = DEFAULT_SSH_USERNAME, port: int = 22
    ) -> None:
        """Initialize the SSH client."""
        self.host = host
        self.port = port
        self.username = username
        self.ssh_client: paramiko.SSHClient | None = None

    async def authenticate(self, password: str) -> None:
        """Authenticate with SSH using password."""
        loop = asyncio.get_running_loop()
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client = self.ssh_client  # Capture for lambda
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
        except paramiko.AuthenticationException as e:
            raise NanoKVMSSHAuthenticationError(
                f"SSH authentication failed: {e}"
            ) from e
        except (paramiko.SSHException, paramiko.BadHostKeyException, OSError) as e:
            raise NanoKVMSSHAuthenticationError(f"SSH connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Close SSH connection."""
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None

    async def run_command(self, command: str, timeout: int = 30) -> str:
        """Run a command via SSH and return output."""
        if not self.ssh_client:
            raise NanoKVMSSHNotConnectedError(
                "SSH not connected, call authenticate first"
            )
        loop = asyncio.get_running_loop()
        try:
            output, error = await asyncio.wait_for(
                loop.run_in_executor(None, self._exec_command_sync, command),
                timeout=timeout,
            )
            if error:
                raise NanoKVMSSHCommandError(f"SSH command error: {error}")
            return output.strip()
        except asyncio.TimeoutError:
            raise NanoKVMSSHCommandError(
                f"SSH command timed out after {timeout} seconds"
            ) from None

    def _exec_command_sync(self, command: str) -> tuple[str, str]:
        """Synchronous SSH command execution."""
        assert self.ssh_client is not None  # Should be set after authenticate()
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        output = stdout.read().decode("utf-8")
        error = stderr.read().decode("utf-8")
        return output, error
