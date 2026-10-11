"""Exception hierarchy and mapping of upstream Staan error responses.

Every message in this module is safe to show to an end user or an LLM: it never contains the
API key, the ``Authorization`` header or the raw response body. The only upstream text that is
ever echoed is the body's ``message`` field, capped at :data:`MAX_UPSTREAM_MESSAGE_LENGTH`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "FATAL_ERRORS",
    "MAX_UPSTREAM_MESSAGE_LENGTH",
    "ConfigurationError",
    "StaanAPIError",
    "StaanAuthenticationError",
    "StaanForbiddenError",
    "StaanKeyDisabledError",
    "StaanMCPError",
    "StaanRateLimitError",
    "StaanResponseError",
    "StaanServerError",
    "StaanTransportError",
    "StaanValidationError",
    "error_from_response",
    "is_fatal",
]

MAX_UPSTREAM_MESSAGE_LENGTH: Final = 500
"""Upper bound, in characters, for upstream ``message`` text copied into an error."""

_ELLIPSIS: Final = "..."
_KEY_DISABLED_CODE: Final = "KEY_DISABLED"
_FORBIDDEN: Final = 403
_FIRST_SERVER_ERROR: Final = 500


class StaanMCPError(Exception):
    """Base class for all errors raised by this package.

    ``str(error)`` is a safe, user-facing message that never contains secrets.
    """


class ConfigurationError(StaanMCPError):
    """The server is misconfigured, for example ``STAAN_API_KEY`` is missing or invalid."""


class StaanAPIError(StaanMCPError):
    """An interaction with the Staan API failed.

    Attributes:
        status_code: HTTP status of the upstream response, or ``None`` if there was none.
        code: Machine-readable upstream error code (for example ``KEY_DISABLED``), if any.
        endpoint: The logical endpoint that failed, ``"search"`` or ``"answer"``.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        endpoint: str = "",
    ) -> None:
        """Create the error.

        Args:
            message: Safe, user-facing description.
            status_code: Upstream HTTP status, if a response was received.
            code: Upstream machine-readable error code, if any.
            endpoint: The logical endpoint that failed.
        """
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.endpoint = endpoint


class StaanAuthenticationError(StaanAPIError):
    """HTTP 401: the API key is missing, unknown or malformed."""


class StaanKeyDisabledError(StaanAPIError):
    """HTTP 403 with ``code=KEY_DISABLED``: the API key exists but is disabled."""


class StaanForbiddenError(StaanAPIError):
    """HTTP 403 without ``KEY_DISABLED``: the key may not use this endpoint."""


class StaanValidationError(StaanAPIError):
    """HTTP 400 or 422: Staan rejected the request parameters."""


class StaanRateLimitError(StaanAPIError):
    """HTTP 429, raised after the retry budget is exhausted."""


class StaanServerError(StaanAPIError):
    """HTTP 5xx (after retries) or any other unexpected status."""


class StaanTransportError(StaanAPIError):
    """Network failure or timeout (after retries); there is no HTTP status."""


class StaanResponseError(StaanAPIError):
    """A 2xx response whose body is not a JSON object of the expected shape."""


FATAL_ERRORS: Final = (StaanAuthenticationError, StaanKeyDisabledError, StaanForbiddenError)
"""Errors that every further request with the same key would hit as well."""


def is_fatal(error: StaanMCPError) -> bool:
    """Tell whether ``error`` makes every other request pointless (key or configuration)."""
    return isinstance(error, (*FATAL_ERRORS, ConfigurationError))


def _parse_body(body: bytes) -> dict[str, Any]:
    """Decode an error body into a dict, returning ``{}`` for anything unusable."""
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _cap(text: str) -> str:
    """Truncate ``text`` to :data:`MAX_UPSTREAM_MESSAGE_LENGTH` characters."""
    if len(text) <= MAX_UPSTREAM_MESSAGE_LENGTH:
        return text
    return text[: MAX_UPSTREAM_MESSAGE_LENGTH - len(_ELLIPSIS)] + _ELLIPSIS


def _message_text(payload: dict[str, Any]) -> str:
    """Extract the capped ``message`` text, joining list messages with ``"; "``."""
    message = payload.get("message")
    if isinstance(message, list):
        parts = [item.strip() for item in message if isinstance(item, str) and item.strip()]
        return _cap("; ".join(parts))
    if isinstance(message, str):
        return _cap(message.strip())
    return ""


def _code(payload: dict[str, Any]) -> str | None:
    """Return the upstream ``code`` if it is a non-empty string."""
    code = payload.get("code")
    return code if isinstance(code, str) and code else None


def _detail(text: str) -> str:
    """Format upstream message text as a message suffix (empty when there is none)."""
    return f" Staan said: {text}" if text else ""


def _auth_message(status_code: int, text: str) -> str:
    """Build the 401 message."""
    return (
        f"Staan authentication failed (HTTP {status_code}). "
        f"Check that STAAN_API_KEY is set to a valid, enabled key.{_detail(text)}"
    )


def _validation_message(status_code: int, text: str) -> str:
    """Build the 400/422 message from the upstream validation text."""
    if text:
        return f"Staan rejected the request: {text}"
    return f"Staan rejected the request (HTTP {status_code})."


def _rate_limit_message(status_code: int, text: str) -> str:
    """Build the 429 message."""
    return f"Staan rate limit reached (HTTP {status_code}). Try again shortly.{_detail(text)}"


def _unexpected_message(status_code: int, text: str) -> str:
    """Build the message for 5xx and any other status without a dedicated class."""
    if status_code >= _FIRST_SERVER_ERROR:
        return f"Staan is temporarily unavailable (HTTP {status_code}). Try again later."
    return f"Staan returned an unexpected response (HTTP {status_code}).{_detail(text)}"


def _forbidden_message(code: str | None, endpoint: str, text: str) -> str:
    """Build the 403 message, telling a disabled key from a missing allowlist entry."""
    if code == _KEY_DISABLED_CODE:
        return "This Staan API key is disabled. Enable it in the Staan console or use another key."
    if endpoint == "answer":
        return (
            "The Answer API is not enabled for this API key (invitation-only, "
            "contact support@staan.ai), or Staan has temporarily disabled it "
            "(HTTP 403 Forbidden). Web search is not affected."
        )
    return (
        "Staan refused the request (HTTP 403 Forbidden): the API key may lack access to "
        f"this endpoint. Check the key in the Staan console.{_detail(text)}"
    )


_STATUS_MAP: Final[dict[int, tuple[type[StaanAPIError], Callable[[int, str], str]]]] = {
    400: (StaanValidationError, _validation_message),
    401: (StaanAuthenticationError, _auth_message),
    422: (StaanValidationError, _validation_message),
    429: (StaanRateLimitError, _rate_limit_message),
}


def _classify(
    status_code: int, code: str | None, text: str, endpoint: str
) -> tuple[type[StaanAPIError], str]:
    """Choose the exception class and user-facing message for a failed response."""
    if status_code == _FORBIDDEN:
        forbidden_cls = StaanKeyDisabledError if code == _KEY_DISABLED_CODE else StaanForbiddenError
        return forbidden_cls, _forbidden_message(code, endpoint, text)
    mapped_cls, build = _STATUS_MAP.get(status_code, (StaanServerError, _unexpected_message))
    return mapped_cls, build(status_code, text)


def error_from_response(
    status_code: int, body: bytes, *, endpoint: Literal["search", "answer"]
) -> StaanAPIError:
    """Map a failed upstream response to the matching exception.

    The body is parsed defensively: it may be empty, not JSON, JSON without ``message``, and
    ``message`` may be a string or a list of strings.

    Args:
        status_code: HTTP status of the upstream response.
        body: Raw response body.
        endpoint: Which logical endpoint was called.

    Returns:
        A :class:`StaanAPIError` subclass instance; the caller decides whether to raise it.
    """
    payload = _parse_body(body)
    code = _code(payload)
    cls, message = _classify(status_code, code, _message_text(payload), endpoint)
    return cls(message, status_code=status_code, code=code, endpoint=endpoint)
