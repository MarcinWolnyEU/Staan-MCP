#!/usr/bin/env python3
"""Build ``staan-mcp-stdio.pyz``, the single-file release bundle of the stdio server.

The bundle is a zip application (``zipapp``) that Python runs directly: ``python
staan-mcp-stdio.pyz``. It holds ``staan_mcp/stdio_server.py`` and the pure-standard-library
modules it imports, nothing else. Which modules those are comes from the import graph, walked from
``stdio_server`` with ``ast``, so the list cannot drift from the code: a new import is bundled, an
import of anything that is neither the standard library nor a package module stops the build.

The build is reproducible: members are written in sorted order with a fixed timestamp, fixed
permissions and no compression (zlib output differs between builds of zlib, and the files are
small), line endings are normalised, and the stdlib ``zipapp`` only prepends the shebang. The same
sources give the same bytes on every machine, so the SHA-256 printed at the end can be compared.

Usage: ``python scripts/build_zipapp.py [OUTPUT]`` (default ``dist-bundle/staan-mcp-stdio.pyz``).
"""

from __future__ import annotations

import ast
import hashlib
import io
import sys
import zipapp
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

ROOT: Final = Path(__file__).resolve().parents[1]
PACKAGE: Final = "staan_mcp"
PACKAGE_DIR: Final = ROOT / "src" / PACKAGE
ENTRY_MODULE: Final = "stdio_server"
INIT_MODULE: Final = "__init__"  # the package itself: always bundled, so its imports are walked
DEFAULT_TARGET: Final = ROOT / "dist-bundle" / "staan-mcp-stdio.pyz"
INTERPRETER: Final = "/usr/bin/env python3"
ZIP_EPOCH: Final = (1980, 1, 1, 0, 0, 0)  # the earliest time a zip member can carry
FILE_MODE: Final = 0o100644 << 16  # a regular file, rw-r--r--
UNIX: Final = 3  # ZipInfo.create_system; the default depends on the host (0 on Windows)
MAIN: Final = '''\
"""Entry point of staan-mcp-stdio.pyz: serve MCP over stdio and exit with the status of main."""

import sys

from staan_mcp.stdio_server import main

sys.exit(main())
'''
"""Not the ``__main__`` that ``zipapp`` writes for ``main=``: that one drops the exit status."""


class BundleError(Exception):
    """The bundle cannot be built: an import is not standard library or package code."""


def _imported_names(tree: ast.AST, origin: Path) -> Iterator[tuple[str, bool]]:
    """Yield ``(dotted name, certain)`` for every import in ``tree``.

    ``from a import b`` may import the module ``a.b`` or the attribute ``b`` of ``a``; only the
    file system can tell, so ``a.b`` is reported as not certain.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, True
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                message = f"{origin.name} uses a relative import; the bundle walker needs absolute"
                raise BundleError(message)
            yield node.module, True
            for alias in node.names:
                yield f"{node.module}.{alias.name}", False


def _package_module(name: str, certain: bool, package_dir: Path) -> str | None:  # noqa: FBT001
    """Return the package module a dotted import name stands for, or ``None``."""
    top, _, rest = name.partition(".")
    if top != PACKAGE:
        if top not in sys.stdlib_module_names:
            message = f"{name} is neither standard library nor a {PACKAGE} module"
            raise BundleError(message)
        return None
    module = rest.partition(".")[0]
    if not module:
        return None  # `import staan_mcp`: the package itself is bundled anyway
    if (package_dir / f"{module}.py").is_file():
        return module
    if certain:
        message = f"{name} is imported but {package_dir / (module + '.py')} does not exist"
        raise BundleError(message)
    return None  # `from staan_mcp import name`: an attribute of the package, not a module


def bundle_modules(package_dir: Path = PACKAGE_DIR) -> list[str]:
    """Return the package modules ``stdio_server`` needs, itself and ``__init__`` included."""
    seen: set[str] = set()
    pending = [ENTRY_MODULE, INIT_MODULE]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = package_dir / f"{module}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, certain in _imported_names(tree, path):
            found = _package_module(name, certain, package_dir)
            if found is not None:
                pending.append(found)
    return sorted(seen)


def _member(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    """A zip member that does not depend on the machine or the moment of the build."""
    info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = UNIX
    info.external_attr = FILE_MODE
    return info, data


def archive_bytes(package_dir: Path = PACKAGE_DIR) -> bytes:
    """Return the zip archive (no shebang yet) with ``__main__.py`` and the package modules."""
    members = [("__main__.py", MAIN.encode())]
    for module in bundle_modules(package_dir):
        source = (package_dir / f"{module}.py").read_bytes().replace(b"\r\n", b"\n")
        members.append((f"{PACKAGE}/{module}.py", source))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in sorted(members):
            archive.writestr(*_member(name, data))
    return buffer.getvalue()


def build(target: Path, package_dir: Path = PACKAGE_DIR) -> str:
    """Write the bundle to ``target`` and return its SHA-256."""
    target.parent.mkdir(parents=True, exist_ok=True)
    zipapp.create_archive(io.BytesIO(archive_bytes(package_dir)), target, interpreter=INTERPRETER)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    """Build the bundle named on the command line (or the default one)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        sys.stderr.write("usage: build_zipapp.py [OUTPUT]\n")
        return 2
    target = Path(args[0]) if args else DEFAULT_TARGET
    try:
        digest = build(target, PACKAGE_DIR)
    except BundleError as exc:
        sys.stderr.write(f"build_zipapp.py: {exc}\n")
        return 1
    sys.stdout.write(f"{target} sha256:{digest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
