"""The Cursor plugin manifest and ``mcp.json`` are well-formed and match the published package."""

from __future__ import annotations

import json
import re
from typing import Any, cast

from repo_files import ROOT, read_text, server_json

from staan_mcp.config import ENV_API_KEY

MANIFEST = ".cursor-plugin/plugin.json"
KEBAB_CASE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


def _load(relative: str) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(read_text(relative)))


def test_the_manifest_name_is_kebab_case_and_matches_the_package() -> None:
    name = _load(MANIFEST)["name"]

    assert KEBAB_CASE.match(name)
    assert name == server_json()["packages"][0]["identifier"]


def test_the_manifest_points_at_the_mcp_config_and_existing_files_by_relative_path() -> None:
    manifest = _load(MANIFEST)

    for key in ("mcpServers", "logo"):
        assert not manifest[key].startswith(("/", ".."))
        assert (ROOT / manifest[key]).is_file()


def test_the_mcp_config_declares_the_schema_at_the_top_level() -> None:
    config = _load("mcp.json")

    assert config["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    assert "$schema" not in config["mcpServers"]


def test_the_mcp_config_launches_the_single_file_stdio_server_from_the_plugin_root() -> None:
    server = _load("mcp.json")["mcpServers"]["staan"]
    (script,) = server["args"]

    assert server["type"] == "stdio"
    assert server["command"] == "python"
    assert server["cwd"] == "./"
    assert not script.startswith(("/", ".."))
    assert (ROOT / script).is_file()


def test_the_mcp_config_reaches_a_local_streamable_http_server() -> None:
    server = _load("mcp.json")["mcpServers"]["staan-http"]

    assert server["type"] == "streamable-http"
    assert server["url"] == "http://127.0.0.1:8000/mcp"


def test_every_placeholder_in_the_mcp_config_is_a_declared_variable() -> None:
    placeholders = set(re.findall(r"\$\{([^}]+)\}", read_text("mcp.json")))
    declared = _load(MANIFEST)["variables"]

    assert placeholders == {ENV_API_KEY}
    assert placeholders <= set(declared["properties"])
    assert declared["required"] == [ENV_API_KEY]
