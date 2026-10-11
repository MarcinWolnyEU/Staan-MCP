"""``stdio_server.py`` as a real process whose client goes away, or presses Ctrl-C.

A client that vanishes closes the read end of the server's stdout. The server must then leave
quietly with status 1: no traceback (from the loop or from a worker thread), no "Exception
ignored" from the flush at exit (which would turn the status into 120), no crash while the
interpreter shuts down. Ctrl-C (SIGINT) must leave quietly with status 130. The same behaviour is
tested in process by ``tests/unit/test_stdio_shutdown.py``; here it is seen from outside.

Nothing sleeps: the client reads the handshake answer before it hangs up, and the fake upstream
holds its answer (``Reply.after``) until the client has closed its end.
"""

from __future__ import annotations

import http.client
import json
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from local_staan import LocalStaan, Reply
from rpc_process import INITIALIZE, RpcProcess, clean_environment, initialize
from stdio_helpers import API_KEY

from staan_mcp import stdio_server

COMMAND = [sys.executable, str(Path(stdio_server.__file__))]
TIMEOUT = 60.0
CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "web_search", "arguments": {"query": "x"}},
}


def wire(*messages: dict[str, object]) -> bytes:
    """The wire form of ``messages``."""
    return b"".join(json.dumps(message).encode() + b"\n" for message in messages)


def start(local_staan: LocalStaan) -> subprocess.Popen[bytes]:
    """Start the server, pointed at ``local_staan``, with all three standard streams piped."""
    env = clean_environment(
        {
            "STAAN_API_KEY": API_KEY,
            "STAAN_BASE_URL": local_staan.base_url,
            "STAAN_MAX_RETRIES": "0",
        }
    )
    return subprocess.Popen(  # noqa: S603
        COMMAND,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def hang_up(process: subprocess.Popen[bytes]) -> None:
    """Behave like a client that has vanished: close the read end of the server's stdout."""
    assert process.stdout is not None
    process.stdout.close()


def finish(process: subprocess.Popen[bytes]) -> tuple[int, bytes]:
    """Close stdin (the client is gone altogether) and return the exit status and stderr."""
    assert process.stdin is not None
    assert process.stderr is not None
    process.stdin.close()
    try:
        err = process.stderr.read()
        status = process.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:  # only a bug leaves the process running
        process.kill()
        raise
    finally:
        process.stderr.close()
    return status, err


def test_a_client_that_hangs_up_before_the_first_reply_ends_the_process_quietly(
    local_staan: LocalStaan,
) -> None:
    process = start(local_staan)
    assert process.stdin is not None
    hang_up(process)

    process.stdin.write(wire(INITIALIZE))
    process.stdin.flush()
    status, err = finish(process)

    assert err == b""  # no traceback, no "Exception ignored in: <stdout>"
    assert status == 1  # not 120, which is what the flush at exit used to turn it into


def test_a_client_that_hangs_up_during_a_call_ends_the_process_quietly(
    local_staan: LocalStaan,
) -> None:
    # The upstream holds its answer until a second request has arrived: the one this test sends
    # after the hang-up, so the worker's reply is always written to a pipe nobody reads.
    local_staan.search = Reply(body=b'{"web":{"results":[]}}', after=2)
    process = start(local_staan)
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(wire(INITIALIZE))
    process.stdin.flush()
    assert json.loads(process.stdout.readline())["id"] == INITIALIZE["id"]
    process.stdin.write(wire({"jsonrpc": "2.0", "method": "notifications/initialized"}, CALL))
    process.stdin.flush()
    local_staan.wait_for_requests(1)  # the call is now waiting for its answer
    assert len(local_staan.requests) == 1
    hang_up(process)

    release = http.client.HTTPConnection("127.0.0.1", urlsplit(local_staan.base_url).port)
    release.request("POST", "/answer", body=b"{}", headers={"Content-Length": "2"})
    release.getresponse().read()
    release.close()
    status, err = finish(process)

    assert err == b""
    assert status == 1


def cannot_interrupt() -> bool:
    """Whether SIGINT is ignored here: a child would inherit that and never see Ctrl-C."""
    return sys.platform == "win32" or signal.getsignal(signal.SIGINT) is signal.SIG_IGN


@pytest.mark.skipif(
    cannot_interrupt(),
    reason="needs POSIX signals, and a SIGINT that is not ignored (a child inherits that)",
)
def test_ctrl_c_ends_the_process_quietly_with_status_130() -> None:
    with RpcProcess(COMMAND, clean_environment({"STAAN_API_KEY": API_KEY})) as process:
        initialize(process)  # the server is now reading its next line

        process.interrupt()
        status = process.close()

        assert process.stderr() == ""  # no KeyboardInterrupt traceback
    assert status == 130  # not -2, "killed by SIGINT"
