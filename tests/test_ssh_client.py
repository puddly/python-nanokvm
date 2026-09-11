"""Tests for NanoKVM SSH access."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import Mock, patch

import paramiko
import pytest

from nanokvm.ssh_client import NanoKVMSSH, NanoKVMSSHCommandError


class _FakeChannel:
    def __init__(self, exit_status: int = 0) -> None:
        self.exit_status = exit_status
        self.closed = False
        self.close_event = threading.Event()

    def recv_exit_status(self) -> int:
        return self.exit_status

    def close(self) -> None:
        self.closed = True
        self.close_event.set()


class _FakeStream:
    def __init__(self, channel: _FakeChannel, data: bytes) -> None:
        self.channel = channel
        self.data = data

    def read(self) -> bytes:
        return self.data


class _CoordinatedStream(_FakeStream):
    def __init__(
        self,
        channel: _FakeChannel,
        data: bytes,
        before_read: threading.Event | None = None,
        signal_on_read: threading.Event | None = None,
    ) -> None:
        super().__init__(channel, data)
        self.before_read = before_read
        self.signal_on_read = signal_on_read

    def read(self) -> bytes:
        if self.signal_on_read is not None:
            self.signal_on_read.set()
        if self.before_read is not None:
            self.before_read.wait(1)
        return super().read()


class _BlockingStream(_FakeStream):
    def read(self) -> bytes:
        self.channel.close_event.wait(1)
        return b""


def _fake_client(
    stdout: Any,
    stderr: Any,
) -> Mock:
    fake_client = Mock()
    fake_client.exec_command.return_value = (Mock(), stdout, stderr)
    return fake_client


async def test_run_command_uses_exit_status_and_allows_stderr_warning() -> None:
    channel = _FakeChannel(exit_status=0)
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = _fake_client(
        _FakeStream(channel, b"ok\n"),
        _FakeStream(channel, b"warning\n"),
    )

    assert await client.run_command("synthetic") == "ok"
    assert channel.recv_exit_status() == 0


async def test_run_command_raises_for_nonzero_exit_status_without_stderr() -> None:
    channel = _FakeChannel(exit_status=42)
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = _fake_client(
        _FakeStream(channel, b"partial output\n"),
        _FakeStream(channel, b""),
    )

    with pytest.raises(NanoKVMSSHCommandError, match="status 42"):
        await client.run_command("synthetic")


async def test_run_command_drains_stdout_and_stderr_concurrently() -> None:
    channel = _FakeChannel(exit_status=0)
    stderr_read = threading.Event()
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = _fake_client(
        _CoordinatedStream(channel, b"out\n", before_read=stderr_read),
        _CoordinatedStream(channel, b"warn\n", signal_on_read=stderr_read),
    )

    assert await client.run_command("synthetic", timeout=0.5) == "out"


async def test_run_command_closes_channel_when_timed_out() -> None:
    channel = _FakeChannel()
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = _fake_client(
        _BlockingStream(channel, b""),
        _BlockingStream(channel, b""),
    )

    with pytest.raises(NanoKVMSSHCommandError, match="timed out"):
        await client.run_command("synthetic", timeout=0.01)

    assert channel.closed


@pytest.mark.asyncio
async def test_authenticate_uses_strict_host_key_policy_by_default() -> None:
    fake_client = Mock()
    with patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client):
        client = NanoKVMSSH("kvm.local")
        await client.authenticate("synthetic-password")

    fake_client.load_system_host_keys.assert_called_once_with()
    policy = fake_client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.RejectPolicy)


@pytest.mark.asyncio
async def test_authenticate_can_explicitly_allow_unknown_host_key() -> None:
    fake_client = Mock()
    with patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client):
        client = NanoKVMSSH("kvm.local", allow_unknown_host_key=True)
        await client.authenticate("synthetic-password")

    policy = fake_client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.AutoAddPolicy)
