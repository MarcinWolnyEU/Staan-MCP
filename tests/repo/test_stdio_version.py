"""The standalone server repeats the package version; it must not be able to drift."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from repo_files import ROOT, pyproject, server_json

from staan_mcp import stdio_server

STDIO = "src/staan_mcp/stdio_server.py"
MANIFEST = "src/hermes/plugin.yaml"
VERSION_LINE = re.compile(r'^VERSION: Final = "(?P<version>[^"]+)"$', re.MULTILINE)
# commitizen reconfigures logging when it is imported (``disable_existing_loggers``), which would
# silence the loggers that other tests read, so the bump runs in a child process.
BUMP = """
import json, sys
from commitizen.bump import update_version_in_files

current, new, files = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
changed = update_version_in_files(current, new, files, check_consistency=True, encoding="utf-8")
print(json.dumps(changed))
"""


def declared_version(text: str) -> str:
    """The version ``stdio_server.py`` declares, read from its source."""
    match = VERSION_LINE.search(text)
    assert match is not None
    return match["version"]


def test_the_standalone_version_is_the_package_version() -> None:
    package = pyproject()["project"]["version"]

    assert declared_version((ROOT / STDIO).read_text(encoding="utf-8")) == package
    assert package == stdio_server.VERSION
    assert server_json()["version"] == package


@pytest.mark.skipif(
    shutil.which("git") is None,
    reason="commitizen runs `git` while it rewrites version files, and git is not on PATH",
)
def test_commitizen_rewrites_the_standalone_version_and_nothing_else(tmp_path: Path) -> None:
    for name in ("server.json", STDIO, MANIFEST):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, target)
    before = (tmp_path / STDIO).read_text(encoding="utf-8").splitlines()
    current = pyproject()["project"]["version"]
    files = pyproject()["tool"]["commitizen"]["version_files"]

    result = subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, "-c", BUMP, current, "9.8.7", json.dumps(files)],
        capture_output=True,
        check=True,
        cwd=tmp_path,
    )

    changed = json.loads(result.stdout)
    assert {Path(name).as_posix() for name in changed} == {"server.json", STDIO, MANIFEST}
    after = (tmp_path / STDIO).read_text(encoding="utf-8")
    assert declared_version(after) == "9.8.7"
    differing = [
        (old, new) for old, new in zip(before, after.splitlines(), strict=True) if old != new
    ]
    assert differing == [(f'VERSION: Final = "{current}"', 'VERSION: Final = "9.8.7"')]
    manifest = (tmp_path / MANIFEST).read_text(encoding="utf-8")
    assert f"\nversion: {current}\n" not in manifest
    assert "\nversion: 9.8.7\n" in manifest
