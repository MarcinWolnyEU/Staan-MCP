"""``staan-mcp-stdio.pyz``, the single-file release bundle, run the way a user runs it.

Every test starts the real archive with ``-I -S`` (no ``site-packages``, no editable install, no
environment variables that steer the import system) from a directory that is not the repository.
"""
# pylint: disable=redefined-outer-name

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from typing import TYPE_CHECKING

import build_zipapp
import pytest
from rpc_process import INITIALIZE, RpcProcess, clean_environment

from staan_mcp import stdio_server
from staan_mcp.contract import TOOL_LIST

if TYPE_CHECKING:
    from pathlib import Path

LOADED_DRIVER = """
import json, sys
sys.path.insert(0, sys.argv[1])
import staan_mcp.stdio_server as server
print(json.dumps({
    "file": server.__file__,
    "modules": sorted(m for m in sys.modules if m.partition(".")[0] == "staan_mcp"),
}))
"""


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The bundle, built once by the build script, as the release builds it."""
    target = tmp_path_factory.mktemp("bundle") / "staan-mcp-stdio.pyz"
    assert build_zipapp.main([str(target)]) == 0
    return target


@pytest.fixture
def elsewhere(tmp_path: Path) -> Path:
    """A working directory that is not the repository."""
    return tmp_path


def run_bundle(
    bundle: Path, cwd: Path, *args: str, stdin: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    """Run the archive on a bare interpreter and wait for it."""
    return subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, "-I", "-S", str(bundle), *args],
        capture_output=True,
        check=False,
        cwd=cwd,
        env=clean_environment({}),
        input=stdin,
    )


def test_the_archive_starts_with_the_portable_shebang(bundle: Path) -> None:
    assert bundle.read_bytes().startswith(b"#!/usr/bin/env python3\n")
    assert zipfile.is_zipfile(bundle)


def test_the_bundle_serves_mcp_from_a_bare_interpreter(bundle: Path, elsewhere: Path) -> None:
    command = [sys.executable, "-I", "-S", str(bundle)]
    with RpcProcess(command, clean_environment({}), cwd=elsewhere) as process:
        initialized = process.call(INITIALIZE)["result"]
        process.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = process.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]
        called = process.call(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "web_search", "arguments": {"query": "python"}},
            }
        )["result"]
        status = process.close()
        error_output = process.stderr()

    assert initialized["serverInfo"] == {"name": "staan", "version": stdio_server.VERSION}
    assert listed == {"tools": TOOL_LIST}
    assert called["isError"] is True
    assert called["content"][0]["text"].startswith(
        "Error executing tool web_search: STAAN_API_KEY is not set."
    )
    assert status == 0
    assert "cannot import the staan_mcp package" not in error_output
    assert "Traceback" not in error_output


def test_the_bundle_reports_its_version(bundle: Path, elsewhere: Path) -> None:
    result = run_bundle(bundle, elsewhere, "--version")

    assert result.returncode == 0
    assert result.stdout.decode().splitlines() == [f"staan-stdio {stdio_server.VERSION}"]
    assert result.stderr == b""


def test_the_exit_status_of_main_reaches_the_shell(bundle: Path, elsewhere: Path) -> None:
    result = run_bundle(bundle, elsewhere, "--bogus")

    assert result.returncode == 2
    assert result.stderr.decode().startswith("usage: staan-mcp-stdio [--version]")


def test_the_package_is_imported_from_inside_the_archive_and_nowhere_else(
    bundle: Path, elsewhere: Path
) -> None:
    result = subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, "-I", "-S", "-c", LOADED_DRIVER, str(bundle)],
        capture_output=True,
        check=True,
        cwd=elsewhere,
        env=clean_environment({}),
    )

    loaded = json.loads(result.stdout)
    assert loaded["file"].startswith(str(bundle))
    with zipfile.ZipFile(bundle) as archive:
        members = set(archive.namelist())
    # Exactly what a run imports: nothing missing (the import would fail) and nothing spare.
    imported = {
        "staan_mcp/__init__.py" if name == "staan_mcp" else name.replace(".", "/") + ".py"
        for name in loaded["modules"]
    }
    assert members - {"__main__.py"} == imported


def test_the_bundle_holds_the_modules_stdio_server_imports(bundle: Path) -> None:
    """Fails when an import of ``stdio_server`` (or of a module it needs) is not in the archive."""
    with zipfile.ZipFile(bundle) as archive:
        members = set(archive.namelist())

    wanted = {f"staan_mcp/{name}.py" for name in build_zipapp.bundle_modules()}
    assert members == wanted | {"__main__.py"}
    assert "staan_mcp/stdio_server.py" in members
