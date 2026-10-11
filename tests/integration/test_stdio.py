"""Smoke test of the real ``staan-mcp`` process over stdio (no network, no API key)."""
# pylint: disable=redefined-outer-name

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
from mcp.types import TextContent

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.anyio


@pytest.fixture
def errlog() -> Iterator[TextIO]:
    """A real file for the server's stderr.

    The SDK's default is bound to ``sys.stderr`` at import time, which some runners (mutmut)
    close before the test body runs.
    """
    with Path(os.devnull).open("w", encoding="utf-8") as handle:
        yield handle


async def test_stdio_process_lists_tools_and_reports_a_missing_key(errlog: TextIO) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "staan_mcp"],
        env={**get_default_environment(), "STAAN_API_KEY": ""},
    )

    with anyio.fail_after(60):
        async with Client(stdio_client(params, errlog=errlog)) as client:
            tools = sorted(tool.name for tool in (await client.list_tools()).tools)
            result = await client.call_tool("web_search", {"query": "python"})

    assert tools == ["answer", "web_search"]
    assert result.is_error is True
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert "STAAN_API_KEY is not set" in block.text
