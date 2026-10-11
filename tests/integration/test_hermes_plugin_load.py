"""The Hermes plugin is loaded the way Hermes loads it: as a directory, by path, in its own process.

``src/hermes/__init__.py`` has to find the ``staan_mcp`` package by itself, because Hermes imports
the directory without putting anything on ``sys.path``. A bare interpreter (``-I -S``: no
site-packages, no editable install, no third-party module) proves both ways it can find it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import mutmut_skip_reason
from rpc_process import clean_environment

SRC = Path(__file__).resolve().parents[2] / "src"
PLUGIN = SRC / "hermes"

# Hermes imports the directory under a name of its own, as a package, so ``.tools`` is relative.
LOADER = """
import importlib.util, json, sys

directory = sys.argv[1]
spec = importlib.util.spec_from_file_location(
    "loaded_plugin", directory + "/__init__.py", submodule_search_locations=[directory]
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class Context:
    names = []

    def register_tool(self, **fields):
        self.names.append(fields["name"])


module.register(Context())
print(json.dumps({"tools": Context.names, "path": sys.path}))
"""

needs_the_pristine_package = pytest.mark.skipif(
    mutmut_skip_reason() is not None, reason=mutmut_skip_reason() or ""
)


def load(
    directory: Path, flags: tuple[str, ...], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Load the plugin directory in a new interpreter and register it with a recording context."""
    return subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, *flags, "-c", LOADER, str(directory)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=directory.parent,
    )


@needs_the_pristine_package
def test_the_plugin_loads_from_a_checkout_in_a_bare_interpreter() -> None:
    result = load(PLUGIN, ("-I", "-S"), clean_environment({}))

    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert loaded["tools"] == ["staan_web_search", "staan_answer"]
    assert str(SRC) in loaded["path"]


@needs_the_pristine_package
def test_a_plugin_directory_on_its_own_uses_the_package_it_finds_on_the_path(
    tmp_path: Path,
) -> None:
    installed = tmp_path / "plugins" / "staan"
    shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__"))

    result = load(installed, ("-S",), clean_environment({"PYTHONPATH": str(SRC)}))

    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert loaded["tools"] == ["staan_web_search", "staan_answer"]


@needs_the_pristine_package
def test_a_plugin_directory_on_its_own_says_which_package_is_missing(tmp_path: Path) -> None:
    installed = tmp_path / "plugins" / "staan"
    shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__"))

    result = load(installed, ("-I", "-S"), clean_environment({}))

    assert result.returncode != 0
    assert "No module named 'staan_mcp'" in result.stderr
