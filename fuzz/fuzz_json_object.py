"""Fuzz target for response decoding: :func:`staan_mcp.upstream.json_object`.

Feeds arbitrary bytes and status codes to :func:`~staan_mcp.upstream.json_object`, the decoder
for successful upstream responses. Its only documented failure is
:class:`~staan_mcp.errors.StaanResponseError` (raised when the body is not valid JSON or not a
JSON object); it must never raise anything else and never crash, and on success it must return
the decoded JSON object unchanged.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final

from staan_mcp.errors import StaanMCPError, StaanResponseError
from staan_mcp.upstream import json_object

if TYPE_CHECKING:
    from staan_mcp.upstream import Endpoint

_ENDPOINTS: Final[tuple[Endpoint, Endpoint]] = ("search", "answer")


def test_one_input(data: bytes) -> None:
    """Fuzz one input as a four-byte little-endian status plus a raw response body.

    Args:
        data: Raw fuzzer input.

    Raises:
        AssertionError: If an invariant of :mod:`staan_mcp.upstream` is violated.
    """
    status = int.from_bytes(data[:4], "little", signed=True)
    content = data[4:]
    for endpoint in _ENDPOINTS:
        outcome: object
        try:
            outcome = json_object(content, status, endpoint)
        except StaanMCPError as exc:
            outcome = exc
        if isinstance(outcome, StaanMCPError):
            assert isinstance(outcome, StaanResponseError)
            assert outcome.status_code == status
            assert outcome.endpoint == endpoint
        else:
            assert isinstance(outcome, dict)


def main() -> None:
    """Run the target under libFuzzer until it stops."""
    import atheris  # noqa: PLC0415  (imported here so the module loads without atheris)

    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
