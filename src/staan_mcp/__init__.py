"""MCP server for the Staan API: web search and grounded answers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("staan-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
