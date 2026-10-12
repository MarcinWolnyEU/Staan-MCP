"""The two Staan tools as native Hermes Agent tools.

`Hermes <https://hermes-agent.nousresearch.com/>`_ loads this directory (``src/hermes``) as a
directory plugin: ``plugin.yaml`` is the manifest and ``__init__.py`` hands :func:`register` to
Hermes. Hermes can reach the same two tools through the MCP server; this is the way that needs no
second process and no MCP client.

Nothing here belongs to the MCP server: the server never imports this directory. It builds on the
package's standard-library modules, like the standalone ``stdio_server.py``: Hermes pins its own
``mcp`` and ``httpx2``, so the plugin must not bring a second set of them. Each call goes through
:func:`staan_mcp.stdio_server.call_tool`, so arguments are checked, requests are made, retried and
rate limited, and errors are worded exactly as for an MCP client. Hermes talks to the plugin in
JSON strings, not in MCP results, which is all this module translates.

Hermes already has a ``web_search`` tool, and a plugin may not take the name of another toolset, so
the tools are ``staan_web_search`` and ``staan_answer``.

The API key and the other ``STAAN_*`` settings are read through Hermes' profile-aware
``agent.secret_scope.get_secret`` when that module exists (so a profile never sees another
profile's key), and from the process environment otherwise.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import threading
from typing import TYPE_CHECKING, Any, Final, Protocol

from staan_mcp import stdio_server
from staan_mcp.config import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_DEADLINE,
    ENV_MAX_CONTENT_CHARS,
    ENV_MAX_RETRIES,
    ENV_RATE_LIMIT,
    ENV_TIMEOUT,
    Settings,
)
from staan_mcp.contract import TOOL_LIST
from staan_mcp.errors import ConfigurationError
from staan_mcp.ratelimit import RateLimiter

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = ["TOOLSET", "TOOL_NAMES", "PluginContext", "register"]

TOOLSET: Final = "staan"
"""Name of the Hermes toolset that holds both tools."""

TOOL_NAMES: Final = {"web_search": "staan_web_search", "answer": "staan_answer"}
"""Hermes name of each server tool; Hermes' own ``web_search`` is taken."""

_EMOJI: Final = {"web_search": "\N{LEFT-POINTING MAGNIFYING GLASS}", "answer": "\N{SPEECH BALLOON}"}
_ENV_NAMES: Final = (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_TIMEOUT,
    ENV_DEADLINE,
    ENV_MAX_RETRIES,
    ENV_RATE_LIMIT,
    ENV_MAX_CONTENT_CHARS,
)
_HERMES_ENV_MODULE: Final = "agent.secret_scope"

_limiters: dict[Settings, RateLimiter] = {}
_limiters_lock = threading.Lock()


class PluginContext(Protocol):  # pylint: disable=too-few-public-methods
    """The part of Hermes' ``PluginContext`` this plugin uses."""

    def register_tool(  # noqa: PLR0913  (Hermes' signature)
        self,
        *,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Callable[..., str],
        check_fn: Callable[[], bool],
        requires_env: list[str],
        description: str,
        emoji: str,
    ) -> object:
        """Add a tool to the Hermes registry."""
        ...


def _reader() -> Callable[[str], str | None]:
    """How to read one variable: Hermes' profile-aware reader if there is one, else the process."""
    try:
        reader: Callable[[str], str | None] = importlib.import_module(_HERMES_ENV_MODULE).get_secret
    except ImportError:  # not running inside Hermes
        return os.environ.get
    return reader


def _environment() -> dict[str, str]:
    """The ``STAAN_*`` variables that are set, as Hermes resolves them for the active profile."""
    read = _reader()
    return {name: value for name in _ENV_NAMES if (value := read(name)) is not None}


def _settings() -> Settings | ConfigurationError:
    """The current settings, or the configuration error that every call will report."""
    try:
        return Settings.from_env(_environment())
    except ConfigurationError as exc:
        return exc


def _has_api_key() -> bool:
    """Whether the tools can work; Hermes hides them from the model while this is false."""
    settings = _settings()
    return isinstance(settings, Settings) and bool(settings.api_key)


def _limiter(settings: Settings) -> RateLimiter:
    """The one rate limiter of every call made with ``settings``, whatever thread it runs on."""
    with _limiters_lock:
        limiter = _limiters.get(settings)
        if limiter is None:
            limiter = _limiters[settings] = RateLimiter(settings.rate_limit)
        return limiter


def _run(tool: str, args: dict[str, Any]) -> str:
    """Run a server tool and return what a Hermes handler returns: a JSON string, never a raise."""
    settings = _settings()
    if isinstance(settings, Settings):
        result = stdio_server.call_tool(tool, args, settings, threading.Event(), _limiter(settings))
    else:
        result = stdio_server.call_tool(tool, args, settings, threading.Event())
    (block,) = result["content"]
    text = str(block["text"])
    return json.dumps({"error": text}, ensure_ascii=False) if result.get("isError") else text


def _handler(tool: str) -> Callable[..., str]:
    """Build the Hermes handler of one server tool."""

    def handle(args: dict[str, Any], **_context: object) -> str:
        return _run(tool, args)

    handle.__name__ = TOOL_NAMES[tool]
    return handle


def _provider_safe(
    node: Any,  # noqa: ANN401  (a JSON Schema node of any shape)
    defs: Mapping[str, Any],
) -> Any:  # noqa: ANN401
    """Copy a JSON Schema without what some model providers reject.

    Optional arguments arrive as ``anyOf: [<type>, {"type": "null"}]`` with ``default: null``,
    which Anthropic refuses. The null branch goes, and a union left with one branch becomes that
    branch; an argument's optionality is in ``required``. A ``$ref`` into ``$defs`` is replaced by
    what it points at, since not every provider resolves references. A ``title`` that is text is
    decoration and goes; a property that is itself called ``title`` stays.
    """
    if isinstance(node, list):
        return [_provider_safe(item, defs) for item in node]
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    if "$ref" in node:
        return _provider_safe(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    branches = node.get("anyOf")
    if isinstance(branches, list):
        kept = [branch for branch in branches if branch != {"type": "null"}]
        node = {key: value for key, value in node.items() if key != "anyOf"}
        if node.get("default") is None:
            node.pop("default", None)
        if len(kept) == 1:
            node = {**kept[0], **node}
        else:
            node["anyOf"] = kept
    return {
        key: _provider_safe(value, defs)
        for key, value in node.items()
        if key != "$defs" and not (key == "title" and isinstance(value, str))
    }


def _schema(tool: dict[str, Any]) -> dict[str, Any]:
    """The Hermes schema of an MCP tool definition: name, description and JSON Schema."""
    return {
        "name": TOOL_NAMES[tool["name"]],
        # The answer's text points at the search tool by its server name.
        "description": str(tool["description"]).replace("web_search", TOOL_NAMES["web_search"]),
        "parameters": _provider_safe(tool["inputSchema"], tool["inputSchema"].get("$defs", {})),
    }


def register(ctx: PluginContext) -> None:
    """Hermes' plugin entry point: add ``staan_web_search`` and ``staan_answer``."""
    for tool in TOOL_LIST:
        schema = _schema(tool)
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=_handler(tool["name"]),
            check_fn=_has_api_key,
            requires_env=[ENV_API_KEY],
            description=str(tool["title"]),
            emoji=_EMOJI[tool["name"]],
        )
