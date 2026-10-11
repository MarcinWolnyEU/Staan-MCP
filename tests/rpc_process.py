"""Drive a real MCP server process over stdio with newline-delimited JSON-RPC.

Used by the tests that must see a server from the outside: the standalone script run with a plain
interpreter, and the comparison of that script with ``python -m staan_mcp``.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
from typing import IO, TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from types import TracebackType

TIMEOUT = 60.0
FENCE_ID = "fence"
INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


def clean_environment(extra: Mapping[str, str]) -> dict[str, str]:
    """The current environment without any ``STAAN_*`` setting, plus ``extra``."""
    base = {key: value for key, value in os.environ.items() if not key.startswith("STAAN_")}
    return {**base, **extra}


class RpcProcess:
    """A server process with a reader thread, so a silent server fails the test, not hangs it."""

    def __init__(
        self, command: Sequence[str], env: Mapping[str, str], cwd: Path | None = None
    ) -> None:
        """Start ``command`` in ``cwd`` with exactly ``env`` as its environment."""
        self._process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            cwd=cwd,
        )
        self._lines: queue.Queue[bytes] = queue.Queue()
        self._stderr: list[bytes] = []
        self._readers = [
            threading.Thread(target=self._pump, args=(self._process.stdout, self._lines.put)),
            threading.Thread(target=self._pump, args=(self._process.stderr, self._stderr.append)),
        ]
        for reader in self._readers:
            reader.start()

    @staticmethod
    def _pump(stream: IO[bytes] | None, sink: Any) -> None:
        assert stream is not None
        for line in stream:
            sink(line)

    def notify(self, message: Mapping[str, Any]) -> None:
        """Send a message that gets no answer."""
        stdin = self._process.stdin
        assert stdin is not None
        stdin.write(json.dumps(message).encode() + b"\n")
        stdin.flush()

    def call(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Send a request and return the next message the server writes."""
        self.notify(message)
        try:
            line = self._lines.get(timeout=TIMEOUT)
        except queue.Empty:
            msg = f"no answer to {message!r}; stderr: {self.stderr()}"
            raise AssertionError(msg) from None
        reply: dict[str, Any] = json.loads(line)
        return reply

    def send_raw(self, line: bytes) -> None:
        """Send ``line`` (and a newline) exactly as given, valid JSON or not."""
        stdin = self._process.stdin
        assert stdin is not None
        stdin.write(line + b"\n")
        stdin.flush()

    def answers_to(self, line: bytes, grace: float = 0.05) -> list[dict[str, Any]]:
        """Send ``line`` and return everything the server writes in answer to it (maybe nothing).

        A ping follows the line and a server answers in order, so what comes before the ping's
        answer belongs to ``line``; ``grace`` seconds more catch an answer that a worker thread
        or task writes late (a tool call).
        """
        self.send_raw(line)
        self.notify({"jsonrpc": "2.0", "id": FENCE_ID, "method": "ping"})
        answers: list[dict[str, Any]] = []
        what = f"answer to the ping after {line!r}"
        while (reply := self._next_message(what)).get("id") != FENCE_ID:
            answers.append(reply)
        while True:
            try:
                answers.append(json.loads(self._lines.get(timeout=grace)))
            except queue.Empty:
                return answers

    def _next_message(self, what: str) -> dict[str, Any]:
        try:
            line = self._lines.get(timeout=TIMEOUT)
        except queue.Empty:
            msg = f"no {what}; stderr: {self.stderr()}"
            raise AssertionError(msg) from None
        message: dict[str, Any] = json.loads(line)
        return message

    def stderr(self) -> str:
        """Everything the server wrote to stderr so far."""
        return b"".join(self._stderr).decode(errors="replace")

    def interrupt(self) -> None:
        """Send SIGINT, as Ctrl-C does (POSIX only)."""
        self._process.send_signal(signal.SIGINT)

    def close(self) -> int:
        """Close stdin, wait for the process to exit and return its status."""
        stdin = self._process.stdin
        assert stdin is not None
        stdin.close()
        try:
            status = self._process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            self._process.kill()
            status = self._process.wait()
        for reader in self._readers:
            reader.join()
        for stream in (self._process.stdout, self._process.stderr):
            assert stream is not None
            stream.close()
        return status

    def __enter__(self) -> Self:
        """Return the running process."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the process."""
        self.close()


def initialize(process: RpcProcess) -> dict[str, Any]:
    """Run the handshake and return the ``initialize`` result."""
    reply = process.call(INITIALIZE)
    process.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
    result: dict[str, Any] = reply["result"]
    return result
