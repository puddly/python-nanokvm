"""Tests for NanoKVM SSH access."""

from __future__ import annotations

import asyncio
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


async def test_concurrent_command_timeout_closes_only_its_own_channel() -> None:
    """Timeout cleanup cannot target the last channel opened by another call."""
    first, second = _FakeChannel(), _FakeChannel()
    first_started, second_started = threading.Event(), threading.Event()

    class BlockingStream(_FakeStream):
        def read(self) -> bytes:
            started = first_started if self.channel is first else second_started
            started.set()
            self.channel.close_event.wait(2)
            return b"done"

    def execute(command: str) -> tuple[Mock, BlockingStream, BlockingStream]:
        channel = first if command == "first" else second
        return Mock(), BlockingStream(channel, b""), BlockingStream(channel, b"")

    client = NanoKVMSSH("synthetic.local")
    fake_client = Mock()
    fake_client.exec_command.side_effect = execute
    client.ssh_client = fake_client
    pending1 = asyncio.create_task(client.run_command("first", timeout=0.2))
    assert await asyncio.to_thread(first_started.wait, 1)
    pending2 = asyncio.create_task(client.run_command("second", timeout=1))
    assert await asyncio.to_thread(second_started.wait, 1)
    try:
        with pytest.raises(NanoKVMSSHCommandError, match="timed out"):
            await pending1
        assert first.closed
        assert not second.closed
        second.close()
        assert await pending2 == "done"
    finally:
        first.close()
        second.close()
        await asyncio.gather(pending1, pending2, return_exceptions=True)


async def test_channel_created_after_timeout_is_closed() -> None:
    """A worker may finish opening its channel after the caller times out."""
    opening, release = threading.Event(), threading.Event()
    channel = _FakeChannel()

    def execute(command: str) -> tuple[Mock, _FakeStream, _FakeStream]:
        opening.set()
        release.wait(2)
        return Mock(), _FakeStream(channel, b""), _FakeStream(channel, b"")

    client = NanoKVMSSH("synthetic.local")
    fake_client = Mock()
    fake_client.exec_command.side_effect = execute
    client.ssh_client = fake_client
    pending = asyncio.create_task(client.run_command("synthetic", timeout=0.1))
    assert await asyncio.to_thread(opening.wait, 1)
    try:
        with pytest.raises(NanoKVMSSHCommandError, match="timed out"):
            await pending
    finally:
        release.set()
    assert await asyncio.to_thread(channel.close_event.wait, 1)


async def test_cancelled_command_closes_its_channel() -> None:
    """Cancelling the asyncio task must also clean up the worker's channel."""
    started = threading.Event()
    channel = _FakeChannel()

    class BlockingStream(_FakeStream):
        def read(self) -> bytes:
            started.set()
            self.channel.close_event.wait(2)
            return b""

    client = NanoKVMSSH("synthetic.local")
    client.ssh_client = _fake_client(
        BlockingStream(channel, b""), _FakeStream(channel, b"")
    )
    pending = asyncio.create_task(client.run_command("synthetic"))
    assert await asyncio.to_thread(started.wait, 1)
    try:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert channel.closed
    finally:
        channel.close()


@pytest.mark.asyncio
async def test_reauthentication_closes_previous_connection_after_success() -> None:
    previous_client = Mock()
    replacement_client = Mock()
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = previous_client

    with patch(
        "nanokvm.ssh_client.paramiko.SSHClient", return_value=replacement_client
    ):
        await client.authenticate("synthetic-password")

    assert client.ssh_client is replacement_client
    previous_client.close.assert_called_once_with()
    replacement_client.close.assert_not_called()


@pytest.mark.asyncio
async def test_authentication_passes_banner_and_authentication_timeouts() -> None:
    fake_client = Mock()
    with patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client):
        await NanoKVMSSH("kvm.local").authenticate("synthetic-password")

    fake_client.connect.assert_called_once_with(
        "kvm.local",
        port=22,
        username="root",
        password="synthetic-password",
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
    )


@pytest.mark.asyncio
async def test_cancelled_authentication_closes_client_after_connect_finishes() -> None:
    connect_started = threading.Event()
    release_connect = threading.Event()
    connect_finished = threading.Event()
    closed_after_connect = threading.Event()
    fake_client = Mock()

    def connect(*_: Any, **__: Any) -> None:
        connect_started.set()
        release_connect.wait(1)
        connect_finished.set()

    close_calls = 0

    def close() -> None:
        nonlocal close_calls
        close_calls += 1
        if close_calls >= 2:
            closed_after_connect.set()

    fake_client.connect.side_effect = connect
    fake_client.close.side_effect = close
    client = NanoKVMSSH("kvm.local")
    with patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client):
        pending = asyncio.create_task(client.authenticate("synthetic-password"))
        assert await asyncio.to_thread(connect_started.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        release_connect.set()

    assert await asyncio.to_thread(connect_finished.wait, 1)
    assert await asyncio.to_thread(closed_after_connect.wait, 1)
    assert fake_client.close.call_count >= 2
    assert client.ssh_client is None


@pytest.mark.asyncio
async def test_transport_failure_is_reported_as_command_error_and_clears_client() -> (
    None
):
    fake_client = Mock()
    fake_client.exec_command.side_effect = paramiko.SSHException("transport dropped")
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = fake_client

    with pytest.raises(NanoKVMSSHCommandError, match="transport dropped"):
        await client.run_command("synthetic")

    assert client.ssh_client is None
    fake_client.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_transport_failure_does_not_clear_replacement_client() -> None:
    command_started = threading.Event()
    release_command = threading.Event()
    failed_client = Mock()

    def execute_command(_: str) -> None:
        command_started.set()
        release_command.wait(1)
        raise paramiko.SSHException("old transport dropped")

    failed_client.exec_command.side_effect = execute_command
    replacement_client = Mock()
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = failed_client
    pending = asyncio.create_task(client.run_command("synthetic"))
    assert await asyncio.to_thread(command_started.wait, 1)
    client.ssh_client = replacement_client
    release_command.set()

    with pytest.raises(NanoKVMSSHCommandError, match="old transport dropped"):
        await pending

    assert client.ssh_client is replacement_client
    failed_client.close.assert_called_once_with()
    replacement_client.close.assert_not_called()
