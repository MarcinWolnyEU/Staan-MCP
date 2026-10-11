"""Smoke test of the real ``staan-mcp`` process over both HTTP transports (no network, no key).

It complements ``test_http_app.py`` (in-process ASGI) by starting the actual console entry point
on a free loopback port and talking to it with the MCP client over the wire.
"""
# pylint: disable=redefined-outer-name

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.anyio

STARTUP_TIMEOUT = 30.0
POLL_INTERVAL = 0.1


def _free_port() -> int:
    """Ask the OS for a free loopback TCP port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_listening(port: int, process: subprocess.Popen[bytes]) -> None:
    """Block until ``port`` accepts connections, failing fast if the process exits."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        assert process.poll() is None, f"server exited early with code {process.returncode}"
        try:
            socket.create_connection(("127.0.0.1", port), timeout=POLL_INTERVAL).close()
        except OSError:
            time.sleep(POLL_INTERVAL)
        else:
            return
    pytest.fail(f"server did not listen on port {port} within {STARTUP_TIMEOUT} seconds")


@pytest.fixture(params=["http", "stateless"])
def server_url(request: pytest.FixtureRequest) -> Iterator[str]:
    """Run ``staan-mcp --transport <param>`` without an API key and yield its MCP endpoint."""
    port = _free_port()
    with subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "staan_mcp", "--transport", request.param, "--port", str(port)],
        env={**os.environ, "STAAN_API_KEY": ""},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            _wait_until_listening(port, process)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            process.terminate()
            process.wait(timeout=10)


async def test_process_serves_tools_and_reports_a_missing_key(server_url: str) -> None:
    with anyio.fail_after(60):
        async with Client(streamable_http_client(server_url)) as client:
            tools = sorted(tool.name for tool in (await client.list_tools()).tools)
            result = await client.call_tool("web_search", {"query": "python"})

    assert tools == ["answer", "web_search"]
    assert result.is_error is True
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert "STAAN_API_KEY is not set" in block.text


async def test_process_rejects_a_foreign_host_header(server_url: str) -> None:
    async with httpx2.AsyncClient() as http:
        response = await http.post(
            server_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Host": "evil.example.com",
            },
        )

    assert response.status_code == 421
