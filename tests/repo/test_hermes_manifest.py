"""The Hermes plugin manifest says what the plugin does; Hermes reads it without importing code."""

from __future__ import annotations

from hermes_helpers import RecordingContext
from repo_files import load_yaml, pyproject

from hermes import tools
from staan_mcp.config import ENV_API_KEY

MANIFEST = "src/hermes/plugin.yaml"


def test_the_manifest_has_the_package_version() -> None:
    assert load_yaml(MANIFEST)["version"] == pyproject()["project"]["version"]


def test_the_manifest_lists_the_tools_the_plugin_registers() -> None:
    context = RecordingContext()
    tools.register(context)

    assert load_yaml(MANIFEST)["provides_tools"] == list(context.registered)


def test_the_manifest_asks_for_the_staan_key_as_a_secret() -> None:
    (requirement,) = load_yaml(MANIFEST)["requires_env"]

    assert requirement["name"] == ENV_API_KEY
    assert requirement["secret"] is True
