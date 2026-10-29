"""Tests for NanoKVM SSH access."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import contextlib
from datetime import UTC, datetime
from pathlib import Path
import socket
import threading
from typing import Any
from unittest.mock import Mock, patch

import paramiko
from paramiko.common import AUTH_FAILED, AUTH_SUCCESSFUL
import pytest

from nanokvm.exceptions import NanoKVMNotSupportedError
from nanokvm.ssh_client import (
    NanoKVMSSH,
    NanoKVMSSHAuthenticationError,
    NanoKVMSSHCommandError,
    NanoKVMSSHConnectionError,
    NanoKVMSSHNotConnectedError,
    SSHMetrics,
    probe_host_key,
)


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
async def test_authenticate_reads_host_keys_off_the_event_loop(
    tmp_path: Path,
) -> None:
    loop_thread = threading.get_ident()
    read_threads: list[int] = []
    fake_client = Mock()
    fake_client.load_system_host_keys.side_effect = lambda: read_threads.append(
        threading.get_ident()
    )
    fake_client.load_host_keys.side_effect = lambda _: read_threads.append(
        threading.get_ident()
    )
    known_hosts = tmp_path / "known_hosts"
    with patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client):
        client = NanoKVMSSH("kvm.local", known_hosts=known_hosts)
        await client.authenticate("synthetic-password")

    fake_client.load_host_keys.assert_called_once_with(str(known_hosts))
    assert len(read_threads) == 2
    assert loop_thread not in read_threads


@pytest.mark.asyncio
async def test_authenticate_reports_unreadable_known_hosts_as_connection_error() -> (
    None
):
    fake_client = Mock()
    fake_client.load_host_keys.side_effect = OSError("No such file")
    with patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client):
        client = NanoKVMSSH("kvm.local", known_hosts="/missing/known_hosts")
        with pytest.raises(NanoKVMSSHConnectionError, match="No such file"):
            await client.authenticate("synthetic-password")

    fake_client.connect.assert_not_called()
    fake_client.close.assert_called_once_with()


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
        allow_agent=False,
        look_for_keys=False,
    )


@pytest.mark.asyncio
async def test_authentication_options_are_configurable() -> None:
    fake_client = Mock()
    client = NanoKVMSSH(
        "kvm.local",
        connect_timeout=3.5,
        banner_timeout=4.5,
        auth_timeout=5.5,
        allow_agent=True,
        look_for_keys=True,
    )
    with patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client):
        await client.authenticate("synthetic-password")

    fake_client.connect.assert_called_once_with(
        "kvm.local",
        port=22,
        username="root",
        password="synthetic-password",
        timeout=3.5,
        banner_timeout=4.5,
        auth_timeout=5.5,
        allow_agent=True,
        look_for_keys=True,
    )


@pytest.mark.asyncio
async def test_connection_failure_has_different_error() -> None:
    fake_client = Mock()
    fake_client.connect.side_effect = OSError("connection refused")
    client = NanoKVMSSH("kvm.local")

    with (
        patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client),
        pytest.raises(NanoKVMSSHConnectionError, match="connection refused"),
    ):
        await client.authenticate("synthetic-password")

    fake_client.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_invalid_password_remains_an_authentication_error() -> None:
    fake_client = Mock()
    fake_client.connect.side_effect = paramiko.AuthenticationException(
        "invalid password"
    )
    client = NanoKVMSSH("kvm.local")

    with (
        patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client),
        pytest.raises(NanoKVMSSHAuthenticationError, match="invalid password"),
    ):
        await client.authenticate("synthetic-password")

    fake_client.close.assert_called_once_with()


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


@pytest.mark.asyncio
async def test_disconnect_supersedes_in_flight_authentication() -> None:
    connect_started = threading.Event()
    release_connect = threading.Event()
    previous_client = Mock()
    replacement_client = Mock()

    def connect(*_: Any, **__: Any) -> None:
        connect_started.set()
        release_connect.wait(1)

    replacement_client.connect.side_effect = connect
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = previous_client

    with patch(
        "nanokvm.ssh_client.paramiko.SSHClient", return_value=replacement_client
    ):
        pending = asyncio.create_task(client.authenticate("synthetic-password"))
        assert await asyncio.to_thread(connect_started.wait, 1)
        await client.disconnect()
        release_connect.set()

        with pytest.raises(NanoKVMSSHNotConnectedError, match="superseded"):
            await pending

    previous_client.close.assert_called_once_with()
    replacement_client.close.assert_called_once_with()
    assert client.ssh_client is None


@pytest.mark.asyncio
async def test_async_context_manager_disconnects_client() -> None:
    fake_client = Mock()
    client = NanoKVMSSH("kvm.local")
    client.ssh_client = fake_client

    async with client as entered:
        assert entered is client

    assert client.ssh_client is None
    fake_client.close.assert_called_once_with()


_METRICS_OUTPUT = {
    "cat /proc/stat": "cpu  1 2 3\nbtime 1761600000\nprocesses 42",
    "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || true": "45843",
    "cat /proc/meminfo": "MemTotal:  262144 kB\nMemAvailable: 65536 kB\n",
    "df -k /": (
        "Filesystem 1K-blocks Used Available Use% Mounted on\n"
        "/dev/mmcblk0p2 7923848 1456520 6115884 19% /"
    ),
}


async def _metrics(outputs: dict[str, str], watchdog: str) -> Any:
    ssh = NanoKVMSSH("kvm.local")

    async def run_command(command: str, timeout: float = 30) -> str:
        return watchdog if "watchdog" in command else outputs[command]

    with patch.object(ssh, "run_command", side_effect=run_command):
        return await ssh.get_metrics()


async def test_get_metrics_parses_device_output() -> None:
    metrics = await _metrics(_METRICS_OUTPUT, "1")

    assert metrics.boot_time == datetime.fromtimestamp(1761600000, UTC)
    assert metrics.cpu_temperature == 45.8
    assert metrics.memory_total == 256
    assert metrics.memory_used_percent == 75
    assert metrics.storage_total == 7738.13
    assert metrics.storage_used_percent == 19
    assert metrics.watchdog_enabled is True


async def test_get_metrics_returns_none_for_unreadable_values() -> None:
    outputs = dict.fromkeys(_METRICS_OUTPUT, "garbage")

    metrics = await _metrics(outputs, "-")

    assert metrics == SSHMetrics()


async def test_set_watchdog_enabled_rejects_firmware_without_watchdog() -> None:
    ssh = NanoKVMSSH("kvm.local")

    with (
        patch.object(ssh, "run_command", return_value="-"),
        pytest.raises(NanoKVMNotSupportedError),
    ):
        await ssh.set_watchdog_enabled(True)


class _PasswordServer(paramiko.ServerInterface):
    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_auth_password(self, username: str, password: str) -> int:
        if password == "synthetic-password":
            return AUTH_SUCCESSFUL
        return AUTH_FAILED


@pytest.fixture(scope="module")
def server_key() -> paramiko.RSAKey:
    return paramiko.RSAKey.generate(2048)


@pytest.fixture
def ssh_server(server_key: paramiko.RSAKey) -> Iterator[int]:
    """Serve SSH handshakes and password logins on a local port."""
    listener = socket.create_server(("127.0.0.1", 0))
    transports: list[paramiko.Transport] = []

    def serve() -> None:
        while True:
            try:
                sock, _ = listener.accept()
            except OSError:
                return
            transport = paramiko.Transport(sock)
            transport.add_server_key(server_key)
            transports.append(transport)
            with contextlib.suppress(paramiko.SSHException, EOFError):
                transport.start_server(server=_PasswordServer())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1]
    listener.close()
    for transport in transports:
        transport.close()
    thread.join(1)


@pytest.mark.asyncio
async def test_probe_host_key_reads_key_without_logging_in(
    ssh_server: int, server_key: paramiko.RSAKey
) -> None:
    host_key = await probe_host_key("127.0.0.1", ssh_server)

    assert host_key.host == "127.0.0.1"
    assert host_key.known_hosts_line == (
        f"[127.0.0.1]:{ssh_server} ssh-rsa {server_key.get_base64()}"
    )
    assert host_key.fingerprint == server_key.fingerprint


@pytest.mark.asyncio
async def test_probe_host_key_reports_unreachable_host() -> None:
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()

    with pytest.raises(NanoKVMSSHConnectionError):
        await probe_host_key("127.0.0.1", port)


@pytest.mark.asyncio
async def test_pinned_host_key_authenticates_under_any_host_name(
    ssh_server: int, server_key: paramiko.RSAKey
) -> None:
    line = f"old-name.local ssh-rsa {server_key.get_base64()}"

    async with NanoKVMSSH("127.0.0.1", port=ssh_server, host_key=line) as ssh:
        await ssh.authenticate("synthetic-password")
        assert ssh.ssh_client is not None


@pytest.mark.asyncio
async def test_pinned_host_key_rejects_a_different_server_key(ssh_server: int) -> None:
    other = paramiko.RSAKey.generate(2048)
    line = f"127.0.0.1 ssh-rsa {other.get_base64()}"

    async with NanoKVMSSH("127.0.0.1", port=ssh_server, host_key=line) as ssh:
        with pytest.raises(NanoKVMSSHConnectionError):
            await ssh.authenticate("synthetic-password")


@pytest.mark.asyncio
async def test_pinned_host_key_skips_system_known_hosts() -> None:
    fake_client = Mock()
    key = paramiko.RSAKey.generate(1024)
    with patch("nanokvm.ssh_client.paramiko.SSHClient", return_value=fake_client):
        client = NanoKVMSSH("kvm.local", host_key=f"x ssh-rsa {key.get_base64()}")
        await client.authenticate("synthetic-password")

    fake_client.load_system_host_keys.assert_not_called()


@pytest.mark.parametrize("line", ["", "not a key line", "host ssh-rsa !!!"])
def test_invalid_host_key_is_rejected_up_front(line: str) -> None:
    with pytest.raises(ValueError):
        NanoKVMSSH("kvm.local", host_key=line)
