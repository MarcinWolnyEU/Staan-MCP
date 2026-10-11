"""Helpers shared by the in-process tests of ``stdio_server.py``."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from staan_mcp import stdio_server
from staan_mcp.config import Settings

if TYPE_CHECKING:
    from local_staan import LocalStaan

API_KEY = "test-key-0123456789"
SEARCH_BODY = {"q": "python", "market": "en-gb", "count": 10, "offset": 0, "extra_snippets": True}
OK_SEARCH = {"web": {"results": [{"title": "T", "url": "https://a.example", "snippet": "S"}]}}
SIMPLE_HIT = {"title": "T", "url": "https://a.example", "snippet": "S"}


def settings_for(upstream: LocalStaan, **overrides: Any) -> Settings:
    """Settings that point at ``upstream``, with no retries and no waiting by default."""
    base = Settings(api_key=API_KEY, base_url=upstream.base_url, timeout=5.0, max_retries=0)
    return replace(base, **overrides)


def run_tool(upstream: LocalStaan, name: str, args: dict[str, Any], **overrides: Any) -> Any:
    """Call a tool and return the ``tools/call`` result."""
    settings = settings_for(upstream, **overrides)
    return stdio_server.call_tool(name, args, settings, threading.Event())


def text_of(result: dict[str, Any]) -> str:
    """The text of the only content block."""
    (block,) = result["content"]
    return str(block["text"])
