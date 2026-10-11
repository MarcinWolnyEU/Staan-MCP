"""Hermes Agent plugin: Staan web search and answer as native Hermes tools.

This directory is separate from the MCP server (``src/staan_mcp``) and is not part of the
``staan-mcp`` wheel. Hermes imports it as a directory plugin; see ``README.md`` for installing it.
The tools are in ``tools.py``, the manifest is ``plugin.yaml``.

The plugin needs the ``staan_mcp`` package. Next to this directory (a checkout of the repository,
installed as a whole or linked into ``~/.hermes/plugins``) it is found there, and otherwise it must
be installed: ``pip install --no-deps staan-mcp`` into Hermes' environment. ``--no-deps``, because
the plugin only uses the package's standard-library modules and Hermes pins its own ``mcp``.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent
if (_SRC / "staan_mcp").is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from .tools import register  # noqa: E402

__all__ = ["register"]
