"""The standalone ``stdio_server.py`` must run with nothing but a plain Python 3.11+.

This is the "download and run" way of using the server. These tests prove the promise from the
outside: the file starts from a source checkout with no installed package and no third-party
module (``-I -S`` removes ``site-packages`` and the editable install), a blocker makes any import
outside the standard library fail loudly, and a static walk over the files it imports finds no
third-party name. The hard rule is that nothing reachable from it imports ``mcp``, ``httpx2``,
``anyio``, ``pydantic`` or ``pyjwt``.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import mutmut_skip_reason
from rpc_process import INITIALIZE, RpcProcess, clean_environment

from staan_mcp import stdio_server
from staan_mcp.contract import TOOL_LIST

SCRIPT = Path(stdio_server.__file__).resolve()
PACKAGE_DIR = SCRIPT.parent

# These four tests look at the package as it is on disk, on a bare interpreter. mutmut's mutated
# copy of it imports mutmut, so they cannot hold there (and would abort mutmut's clean run); they
# run in every other run. ``tests/unit/test_conftest.py`` checks when this skips.
needs_the_pristine_package = pytest.mark.skipif(
    mutmut_skip_reason() is not None, reason=mutmut_skip_reason() or ""
)

THIRD_PARTY = ("mcp", "httpx2", "httpx", "anyio", "pydantic", "pydantic_core", "jwt", "starlette")
# Everything the script may import from the package: the modules that use only the standard
# library. Adding to this set is a deliberate decision, not an accident.
SHARED_MODULES = {
    "config",
    "contract",
    "domains",
    "errors",
    "formatting",
    "markets",
    "payloads",
    "ratelimit",
    "sources",
    "upstream",
}

CENSUS_DRIVER = """
import json, runpy, sys
allowed = set(sys.stdlib_module_names) | {"staan_mcp", "__main__"}


class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] not in allowed:
            raise ImportError(f"third-party import blocked: {name}")
        return None


sys.meta_path.insert(0, Blocker())
try:
    runpy.run_path(sys.argv[1], run_name="__main__")
except SystemExit:
    pass
loaded = sorted(sys.modules)
print(json.dumps({
    "third_party": [m for m in loaded if m.partition(".")[0] not in allowed],
    "package": [m for m in loaded if m.partition(".")[0] == "staan_mcp"],
}), file=sys.stderr)
"""


def test_the_package_is_not_importable_where_the_script_runs() -> None:
    """The precondition of the next tests: ``-I -S`` really is a bare interpreter."""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", "import staan_mcp"],
        capture_output=True,
        check=False,
        cwd=PACKAGE_DIR.parents[1],
    )

    assert result.returncode != 0
    assert b"ModuleNotFoundError" in result.stderr


@needs_the_pristine_package
def test_the_script_serves_mcp_from_a_bare_interpreter() -> None:
    command = [sys.executable, "-I", "-S", str(SCRIPT)]
    with RpcProcess(command, clean_environment({})) as process:
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

    assert initialized["serverInfo"] == {"name": "staan", "version": stdio_server.VERSION}
    assert listed == {"tools": TOOL_LIST}
    assert called["isError"] is True
    assert called["content"][0]["text"].startswith(
        "Error executing tool web_search: STAAN_API_KEY is not set."
    )
    assert status == 0


@needs_the_pristine_package
def test_the_script_runs_from_any_working_directory(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, "-I", "-S", str(SCRIPT), "--version"],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=clean_environment({}),
    )

    assert result.returncode == 0
    assert result.stdout.decode().splitlines() == [f"staan-stdio {stdio_server.VERSION}"]


def test_a_copy_of_the_file_alone_explains_itself_and_exits_with_status_2(tmp_path: Path) -> None:
    """The file needs its sibling modules; taken out of the repository it says so, once."""
    lone = tmp_path / "stdio_server.py"
    shutil.copy(SCRIPT, lone)

    result = subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, "-I", "-S", str(lone)],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=clean_environment({}),
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 2
    assert result.stdout == b""
    error = result.stderr.decode()
    assert error.startswith("staan-mcp: cannot import the staan_mcp package.")
    assert error.count("\n") == 1
    assert str(tmp_path) in error
    assert "Traceback" not in error
    assert "ModuleNotFoundError" not in error


@needs_the_pristine_package
def test_no_module_outside_the_standard_library_is_imported() -> None:
    result = subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, "-I", "-S", "-c", CENSUS_DRIVER, str(SCRIPT), "--version"],
        capture_output=True,
        check=False,
        env=clean_environment({}),
    )

    census = json.loads(result.stderr.decode().splitlines()[-1])
    assert census["third_party"] == []
    imported = {name.partition(".")[2] for name in census["package"]} - {""}
    assert imported == SHARED_MODULES


def imports_of(path: Path) -> set[str]:
    """The top-level names (and ``staan_mcp`` submodules) a file imports, in any block."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            if node.module == "staan_mcp":
                names.update(f"staan_mcp.{alias.name}" for alias in node.names)
    return names


@needs_the_pristine_package
def test_everything_the_script_imports_is_standard_library_or_shared() -> None:
    pending = [SCRIPT]
    seen: set[str] = set()
    while pending:
        path = pending.pop()
        for name in imports_of(path):
            top = name.partition(".")[0]
            if top == "staan_mcp":
                module = name.partition(".")[2]
                if module and module not in seen:
                    seen.add(module)
                    pending.append(PACKAGE_DIR / f"{module}.py")
            else:
                assert top in sys.stdlib_module_names, f"{path.name} imports {name}"
                assert top not in THIRD_PARTY

    assert seen == SHARED_MODULES


# The names ``typing`` already has on Python 3.8: the ones the script imports before it checks the
# version, so that an old interpreter reads "needs Python 3.11 or newer" and not an ImportError.
TYPING_ON_OLD_PYTHONS = {"TYPE_CHECKING", "Any", "BinaryIO", "Final", "NoReturn", "TypeVar"}


def test_what_the_script_imports_from_typing_exists_on_old_pythons() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in tree.body  # not the ``if TYPE_CHECKING:`` block, which never runs
        if isinstance(node, ast.ImportFrom) and node.module == "typing"
        for alias in node.names
    }

    assert imported
    assert imported <= TYPING_ON_OLD_PYTHONS


@pytest.mark.parametrize("name", ["client", "server", "auth", "cli"])
def test_the_modules_that_need_third_party_packages_do_import_them(name: str) -> None:
    """The counterpart of the test above: it would pass vacuously if these were pure."""
    imported = {top.partition(".")[0] for top in imports_of(PACKAGE_DIR / f"{name}.py")}

    assert imported & set(THIRD_PARTY)
