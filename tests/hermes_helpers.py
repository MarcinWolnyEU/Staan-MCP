"""A stand-in for Hermes' ``PluginContext``, shared by the tests of the Hermes plugin."""

from __future__ import annotations

from typing import Any


class RecordingContext:  # pylint: disable=too-few-public-methods
    """Keeps what the plugin registers, by tool name."""

    def __init__(self) -> None:
        """Start with nothing registered."""
        self.registered: dict[str, dict[str, Any]] = {}

    def register_tool(self, **fields: Any) -> object:
        """Record one tool, as Hermes' registry would take it."""
        self.registered[fields["name"]] = fields
        return fields
