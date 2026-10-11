"""How ``stdio_server.py`` ends: a client that vanishes, Ctrl-C, and the calls still running.

A client that closes its end of our stdout makes every write fail (``BrokenPipeError``). The
server must then stop at once and quietly: no traceback from the loop or from a worker thread, no
"Exception ignored" from the flush at exit (status 120), no call left running (its thread would
write to stderr while the interpreter shuts down: SIGABRT) and a status that says what happened.
Nothing here sleeps: every ordering is forced with events.
"""
# pylint: disable=protected-access

from __future__ import annotations

import io
import json
import os
import sys
import threading
from typing import TYPE_CHECKING, Any

import pytest
from stdio_helpers import API_KEY

from staan_mcp import stdio_server
from staan_mcp.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from _typeshed import ReadableBuffer

WAIT = 30.0
"""The limit of a wait that ends at once when the test passes (a failure waits this long)."""

HANDSHAKE_ID = "handshake"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": HANDSHAKE_ID,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "1.0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
PING = {"jsonrpc": "2.0", "id": 9, "method": "ping"}
CALL_ID = 2
CALL = {
    "jsonrpc": "2.0",
    "id": CALL_ID,
    "method": "tools/call",
    "params": {"name": "web_search", "arguments": {"query": "x"}},
}
LATE = {"content": [{"type": "text", "text": "late"}]}
UNUSED = ConfigurationError("never used: the tool is replaced")


def lines(*messages: dict[str, Any]) -> bytes:
    """The wire form of ``messages``."""
    return b"".join(json.dumps(message).encode() + b"\n" for message in messages)


class HungUp(io.BytesIO):
    """A stdout whose reader has gone away: every write after the first ``ok`` fails."""

    def __init__(self, error: Exception, ok: int = 0) -> None:
        """Fail with ``error`` from write number ``ok + 1`` on."""
        super().__init__()
        self._error = error
        self._ok = ok
        self.attempts = 0

    def write(self, buffer: ReadableBuffer, /) -> int:
        """Count the attempt and fail, or store the bytes."""
        self.attempts += 1
        if self.attempts > self._ok:
            raise self._error
        return super().write(buffer)


class GatedStdout(io.BytesIO):
    """A stdout that holds back the reply to ``CALL`` until the test lets it go."""

    def __init__(self) -> None:
        """Create the gate: ``writing`` is set when the reply is in progress."""
        super().__init__()
        self.writing = threading.Event()
        self.release = threading.Event()

    def write(self, buffer: ReadableBuffer, /) -> int:
        """Hold the reply to ``CALL``; store everything else at once."""
        if bytes(buffer).startswith(b'{"jsonrpc":"2.0","id":%d,' % CALL_ID):
            self.writing.set()
            self.release.wait(WAIT)
        return super().write(buffer)


class EofWhen(io.BytesIO):
    """A stdin that reports the end of the input only once ``gate`` is set."""

    def __init__(self, data: bytes, gate: threading.Event) -> None:
        """Serve ``data``, then wait for ``gate`` before the end of the file."""
        super().__init__(data)
        self._gate = gate

    def readline(self, size: int | None = -1, /) -> bytes:
        """Read a line; at the end of the data, wait for the gate."""
        line = super().readline(size)
        if not line:
            self._gate.wait(WAIT)
        return line


class InterruptedStdin(io.BytesIO):
    """A stdin that raises ``KeyboardInterrupt`` instead of reporting the end of the input."""

    def readline(self, size: int | None = -1, /) -> bytes:
        """Read a line; at the end of the data, behave like Ctrl-C."""
        line = super().readline(size)
        if not line:
            raise KeyboardInterrupt
        return line


class SlowTool:
    """A replacement for ``call_tool`` that runs until its call is told to stop."""

    def __init__(self) -> None:
        """No call has started yet."""
        self.threads: list[threading.Thread] = []
        self.stopped = threading.Event()

    def __call__(self, *args: object) -> dict[str, Any]:
        """Wait for the stop signal (the fourth argument), then return a result nobody wants."""
        stop = args[3]
        assert isinstance(stop, threading.Event)
        self.threads.append(threading.current_thread())
        if stop.wait(WAIT):
            self.stopped.set()
        return LATE

    def finish(self) -> None:
        """Wait until the worker thread has really ended."""
        assert self.stopped.wait(WAIT)
        self.threads[0].join(WAIT)
        assert not self.threads[0].is_alive()


@pytest.fixture(autouse=True)
def reopen_log() -> Iterator[None]:
    """``_close_log`` is for good in a process; give every test a log that works."""
    yield
    stdio_server._LOG_CLOSED.clear()


@pytest.fixture
def serve_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment of a server with a key and no other setting."""
    for name in [name for name in os.environ if name.startswith("STAAN_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("STAAN_API_KEY", API_KEY)


def server_for(stdin: io.BytesIO, stdout: io.BytesIO) -> stdio_server.Server:
    """A server on the two fake streams."""
    return stdio_server.Server(UNUSED, stdin, stdout)


# --------------------------------------------------------------------------------------
# The client is gone
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        BrokenPipeError(32, "Broken pipe"),
        ConnectionResetError(104, "Connection reset by peer"),
        OSError(22, "Invalid argument"),
        ValueError("write to closed file"),
    ],
)
def test_a_failed_write_stops_the_loop_before_the_next_message(error: Exception) -> None:
    stdin = io.BytesIO(lines(INITIALIZE, PING, PING))
    stdout = HungUp(error)
    server = server_for(stdin, stdout)

    assert server.serve() is False

    assert stdout.attempts == 1  # the answer to ``initialize``
    assert stdin.read() == lines(PING, PING)  # neither ping was even read


def test_nothing_is_written_once_the_client_is_gone() -> None:
    stdout = HungUp(BrokenPipeError(32, "Broken pipe"))
    server = server_for(io.BytesIO(lines(INITIALIZE)), stdout)
    server.serve()

    server._reply(1, {})
    server._error(1, -32601, "Method not found")

    assert stdout.attempts == 1


def test_a_call_in_flight_is_told_to_stop_when_the_client_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = SlowTool()
    monkeypatch.setattr(stdio_server, "call_tool", tool)
    stdout = HungUp(BrokenPipeError(32, "Broken pipe"), ok=1)  # the ping reply is the one lost
    server = server_for(io.BytesIO(lines(INITIALIZE, INITIALIZED, CALL, PING)), stdout)

    assert server.serve() is False  # does not wait for the call: nobody wants its result

    tool.finish()
    assert stdout.attempts == 2  # the call's late result was never written


def test_a_reply_that_fails_on_a_worker_thread_ends_the_session_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stdio_server, "call_tool", lambda *_args: LATE)
    stdout = HungUp(BrokenPipeError(32, "Broken pipe"), ok=1)  # the call's reply is the one lost
    server = server_for(io.BytesIO(lines(INITIALIZE, INITIALIZED, CALL)), stdout)

    # The end of the input is reached with the call possibly still running; serve joins it. An
    # exception escaping the worker would fail this test (pytest turns it into a warning).
    assert server.serve() is False

    assert stdout.attempts == 2


def test_serve_still_returns_true_when_the_client_stays_until_the_end() -> None:
    stdout = io.BytesIO()

    assert server_for(io.BytesIO(lines(INITIALIZE, PING)), stdout).serve() is True
    assert len(stdout.getvalue().splitlines()) == 2


# --------------------------------------------------------------------------------------
# A worker that is writing its reply is still waited for at the end of the input
# --------------------------------------------------------------------------------------


def test_serve_waits_for_a_worker_that_is_in_the_middle_of_its_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stdio_server, "call_tool", lambda *_args: LATE)
    stdout = GatedStdout()
    # The end of the input comes only once the reply is being written.
    stdin = EofWhen(lines(INITIALIZE, INITIALIZED, CALL), stdout.writing)
    server = server_for(stdin, stdout)
    outcome: list[bool] = []

    def serve() -> None:
        outcome.append(server.serve())

    serving = threading.Thread(target=serve)
    serving.start()
    try:
        assert stdout.writing.wait(WAIT)
        # The invariant: a worker leaves the registry only after its reply is written, or the
        # snapshot ``serve`` takes at the end of the input could miss it.
        assert list(server._workers) == [stdio_server._request_key(CALL_ID)]
        assert not server._inflight
    finally:
        stdout.release.set()
        serving.join(WAIT)

    assert not serving.is_alive()
    assert outcome == [True]
    assert b'"text":"late"' in stdout.getvalue()  # serve returned only after the reply was written
    assert not server._workers


# --------------------------------------------------------------------------------------
# close(): the process is leaving
# --------------------------------------------------------------------------------------


def test_close_stops_all_output_and_every_call_in_flight() -> None:
    stdin = io.BytesIO(lines(PING))
    stdout = io.BytesIO()
    server = server_for(stdin, stdout)
    stop = threading.Event()
    server._inflight[stdio_server._request_key(CALL_ID)] = stop

    server.close()

    assert stop.is_set()
    server._reply(1, {})
    assert stdout.getvalue() == b""
    assert server.serve() is False
    assert stdin.read() == lines(PING)  # not even read


# --------------------------------------------------------------------------------------
# The log is closed for good on the way out
# --------------------------------------------------------------------------------------


def test_the_log_writes_nothing_once_it_is_closed(capsys: pytest.CaptureFixture[str]) -> None:
    stdio_server._log("before")
    stdio_server._close_log()
    stdio_server._log("after")

    assert capsys.readouterr().err == "staan-stdio: before\n"


# --------------------------------------------------------------------------------------
# main(): the exit status, and silence
# --------------------------------------------------------------------------------------


def run_main(monkeypatch: pytest.MonkeyPatch, stdin: io.BytesIO, stdout: io.BytesIO) -> int:
    """Run ``main()`` on fake standard streams."""
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(stdin))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stdout))
    return stdio_server.main([])


@pytest.mark.usefixtures("serve_env")
def test_main_exits_with_status_1_and_prints_nothing_when_the_client_hung_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdout = HungUp(BrokenPipeError(32, "Broken pipe"))

    status = run_main(monkeypatch, io.BytesIO(lines(INITIALIZE, PING)), stdout)

    assert status == 1
    assert stdout.attempts == 1
    assert capsys.readouterr().err == ""
    assert stdio_server._LOG_CLOSED.is_set()  # no thread may write to stderr from now on


@pytest.mark.usefixtures("serve_env")
def test_main_exits_with_status_130_and_prints_nothing_on_ctrl_c(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = SlowTool()
    monkeypatch.setattr(stdio_server, "call_tool", tool)
    stdout = io.BytesIO()

    status = run_main(monkeypatch, InterruptedStdin(lines(INITIALIZE, INITIALIZED, CALL)), stdout)

    assert status == 130
    tool.finish()  # the call in flight was told to stop
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [message["id"] for message in messages] == [HANDSHAKE_ID]  # the late result is dropped
    assert capsys.readouterr().err == ""
    assert stdio_server._LOG_CLOSED.is_set()


@pytest.mark.usefixtures("serve_env")
def test_main_exits_with_status_0_when_the_client_closes_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status = run_main(monkeypatch, io.BytesIO(lines(INITIALIZE, PING)), io.BytesIO())

    assert status == 0
    assert capsys.readouterr().err == ""
    assert not stdio_server._LOG_CLOSED.is_set()


# --------------------------------------------------------------------------------------
# File descriptor 1 after the client has gone
# --------------------------------------------------------------------------------------


def test_discard_stdout_sends_later_writes_to_the_null_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "stdout.bin"
    with target.open("wb") as stream, monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", stream)
        stream.write(b"kept")
        stream.flush()

        stdio_server._discard_stdout()
        stream.write(b"lost")
        stream.flush()

    assert target.read_bytes() == b"kept"


def test_discard_stdout_leaves_a_stream_without_a_descriptor_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    stdio_server._discard_stdout()  # ``fileno()`` raises; nothing to redirect, nothing raised
