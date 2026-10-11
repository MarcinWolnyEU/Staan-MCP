"""``scripts/build_zipapp.py``: which modules are bundled, and that the build is reproducible."""

from __future__ import annotations

import os
import runpy
import zipfile
from typing import TYPE_CHECKING

import build_zipapp
import pytest

if TYPE_CHECKING:
    from pathlib import Path

SHEBANG = b"#!/usr/bin/env python3\n"


def make_package(root: Path, **modules: str) -> Path:
    """A fake ``staan_mcp`` directory with an empty ``__init__`` and the given modules."""
    package = root / "staan_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, source in modules.items():
        (package / f"{name}.py").write_bytes(source.encode())
    return package


def test_the_bundle_is_the_import_graph_of_stdio_server() -> None:
    modules = build_zipapp.bundle_modules()

    assert modules == sorted(modules)
    assert {"__init__", "stdio_server"} <= set(modules)
    # Never the modules that need the MCP SDK, httpx2, pydantic or pyjwt.
    assert not {"client", "server", "auth", "cli", "__main__"} & set(modules)


def test_the_graph_follows_every_form_of_package_import(tmp_path: Path) -> None:
    package = make_package(
        tmp_path,
        stdio_server=(
            "import os\n"
            "import staan_mcp\n"
            "import staan_mcp.first\n"
            "from staan_mcp import __version__, second\n"
            "from staan_mcp.third import thing\n"
        ),
        first="from staan_mcp.shared import x\n",
        second="from staan_mcp.shared import y\n",
        third="",
        shared="",
        unused="import not_a_real_package\n",  # not imported by anything: never looked at
    )

    assert build_zipapp.bundle_modules(package) == [
        "__init__",
        "first",
        "second",
        "shared",
        "stdio_server",
        "third",
    ]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("import httpx2\n", "httpx2 is neither standard library nor a staan_mcp module"),
        ("from pydantic import BaseModel\n", "pydantic is neither"),
        ("from staan_mcp.missing import x\n", "staan_mcp.missing is imported but "),
        ("from . import sibling\n", "stdio_server.py uses a relative import"),
        ("from .sibling import x\n", "stdio_server.py uses a relative import"),
    ],
)
def test_an_import_that_cannot_be_bundled_stops_the_build(
    tmp_path: Path, source: str, message: str
) -> None:
    package = make_package(tmp_path, stdio_server=source)

    with pytest.raises(build_zipapp.BundleError, match=message):
        build_zipapp.bundle_modules(package)


def test_the_package_init_is_checked_too(tmp_path: Path) -> None:
    package = make_package(tmp_path, stdio_server="")
    (package / "__init__.py").write_text("import requests\n", encoding="utf-8")

    with pytest.raises(build_zipapp.BundleError, match="requests is neither"):
        build_zipapp.bundle_modules(package)


def test_the_archive_holds_the_entry_point_and_the_modules_in_sorted_order(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path, stdio_server="from staan_mcp.b import x\n", b="", a="")
    target = tmp_path / "out" / "bundle.pyz"

    build_zipapp.build(target, package)

    with zipfile.ZipFile(target) as archive:
        infos = archive.infolist()
        main = archive.read("__main__.py").decode()
    assert [info.filename for info in infos] == [
        "__main__.py",
        "staan_mcp/__init__.py",
        "staan_mcp/b.py",
        "staan_mcp/stdio_server.py",
    ]
    assert "from staan_mcp.stdio_server import main" in main
    assert "sys.exit(main())" in main
    for info in infos:
        assert info.date_time == build_zipapp.ZIP_EPOCH
        assert info.compress_type == zipfile.ZIP_STORED
        assert info.create_system == build_zipapp.UNIX
        assert info.external_attr == build_zipapp.FILE_MODE
    assert target.read_bytes().startswith(SHEBANG)


def test_line_endings_do_not_change_the_bytes(tmp_path: Path) -> None:
    unix = make_package(tmp_path / "unix", stdio_server="x = 1\ny = 2\n")
    windows = make_package(tmp_path / "windows", stdio_server="x = 1\r\ny = 2\r\n")

    assert build_zipapp.archive_bytes(unix) == build_zipapp.archive_bytes(windows)


def test_the_build_is_reproducible(tmp_path: Path) -> None:
    package = make_package(tmp_path, stdio_server="from staan_mcp.b import x\n", b="x = 1\n")
    first = tmp_path / "first.pyz"
    digest = build_zipapp.build(first, package)
    for path in package.iterdir():
        os.utime(path, (1_000_000_000, 1_000_000_000))  # another checkout, another mtimes
    second = tmp_path / "elsewhere" / "second.pyz"

    assert build_zipapp.build(second, package) == digest
    assert second.read_bytes() == first.read_bytes()


def test_main_builds_to_the_given_path_and_prints_the_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "x" / "staan-mcp-stdio.pyz"

    assert build_zipapp.main([str(target)]) == 0

    digest = build_zipapp.build(tmp_path / "again.pyz")
    assert capsys.readouterr().out == f"{target} sha256:{digest}\n"
    assert target.is_file()


def test_main_builds_to_the_default_path_without_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    default = tmp_path / "dist-bundle" / "staan-mcp-stdio.pyz"
    monkeypatch.setattr(build_zipapp, "DEFAULT_TARGET", default)
    monkeypatch.setattr("sys.argv", ["build_zipapp.py"])

    assert build_zipapp.main() == 0

    assert default.is_file()
    assert capsys.readouterr().out.startswith(f"{default} sha256:")


def test_main_rejects_extra_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert build_zipapp.main(["one", "two"]) == 2

    assert capsys.readouterr().err == "usage: build_zipapp.py [OUTPUT]\n"


def test_main_reports_a_bundle_error_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = make_package(tmp_path, stdio_server="import httpx2\n")
    monkeypatch.setattr(build_zipapp, "PACKAGE_DIR", package)

    assert build_zipapp.main([str(tmp_path / "out.pyz")]) == 1

    assert capsys.readouterr().err == (
        "build_zipapp.py: httpx2 is neither standard library nor a staan_mcp module\n"
    )
    assert not (tmp_path / "out.pyz").exists()


def test_the_script_runs_as_a_program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "program.pyz"
    monkeypatch.setattr("sys.argv", ["build_zipapp.py", str(target)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(build_zipapp.ROOT / "scripts" / "build_zipapp.py"), run_name="__main__")

    assert excinfo.value.code == 0
    build_zipapp.build(tmp_path / "direct.pyz")
    assert target.read_bytes() == (tmp_path / "direct.pyz").read_bytes()
