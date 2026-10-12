"""Fuzz target for upstream error mapping: :func:`staan_mcp.errors.error_from_response`.

Feeds arbitrary status codes and raw response bodies to
:func:`~staan_mcp.errors.error_from_response` for both endpoints. The function must never raise
and must return a :class:`~staan_mcp.errors.StaanAPIError` whose status, endpoint and code match
the input. Per the module contract the only upstream text ever echoed into the message is the
body's ``message`` field (and ``code`` steers the class), so the error for any body must be
identical to the error for a canonical body carrying nothing but the extracted ``message`` and
``code``: anything else the body contains must never leak into the message. ``message`` text is
extracted exactly as documented: a string is stripped; a list is stripped per item, keeps only
non-blank strings and is joined with ``"; "``; anything else contributes nothing.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Final

from staan_mcp.errors import StaanAPIError, error_from_response

if TYPE_CHECKING:
    from staan_mcp.upstream import Endpoint

_ENDPOINTS: Final[tuple[Endpoint, Endpoint]] = ("search", "answer")


def _echo_fields(body: bytes) -> tuple[str, str | None]:
    """Return the ``(message text, code)`` pair the error contract may echo from ``body``."""
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        return "", None
    if not isinstance(payload, dict):
        return "", None
    message = payload.get("message")
    if isinstance(message, list):
        text = "; ".join(item.strip() for item in message if isinstance(item, str) and item.strip())
    elif isinstance(message, str):
        text = message.strip()
    else:
        text = ""
    code = payload.get("code")
    return text, code if isinstance(code, str) and code else None


def _assert_no_body_leak(status: int, body: bytes, endpoint: Endpoint) -> None:
    """Assert ``body`` yields the same error as a body holding only its echo fields.

    Args:
        status: HTTP status of the fuzzed upstream response.
        body: Raw fuzzed upstream response body.
        endpoint: Which logical endpoint was called.

    Raises:
        AssertionError: If an invariant of :mod:`staan_mcp.errors` is violated.
    """
    text, code = _echo_fields(body)
    canonical = json.dumps({"message": text, "code": code}).encode()
    err = error_from_response(status, body, endpoint=endpoint)
    expected = error_from_response(status, canonical, endpoint=endpoint)
    assert isinstance(err, StaanAPIError)
    assert type(err) is type(expected)  # the exact class is part of the contract
    assert str(err) == str(expected)
    assert err.code == expected.code
    assert err.status_code == status
    assert err.endpoint == endpoint
    assert str(err)


def test_one_input(data: bytes) -> None:
    """Fuzz one input as a four-byte little-endian status plus a raw response body.

    Args:
        data: Raw fuzzer input.

    Raises:
        AssertionError: If an invariant of :mod:`staan_mcp.errors` is violated.
    """
    status = int.from_bytes(data[:4], "little", signed=True)
    for endpoint in _ENDPOINTS:
        _assert_no_body_leak(status, data[4:], endpoint)


def main() -> None:
    """Run the target under libFuzzer until it stops."""
    import atheris  # noqa: PLC0415  (imported here so the module loads without atheris)

    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
