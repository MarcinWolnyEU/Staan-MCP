"""Tests for the exception hierarchy and the upstream error mapping."""
# Exact-class assertions (type(x) is Y) are deliberate: subclasses must not satisfy them.
# pylint: disable=unidiomatic-typecheck

from __future__ import annotations

import json
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from staan_mcp import errors
from staan_mcp.errors import (
    FATAL_ERRORS,
    MAX_UPSTREAM_MESSAGE_LENGTH,
    ConfigurationError,
    StaanAPIError,
    StaanAuthenticationError,
    StaanForbiddenError,
    StaanKeyDisabledError,
    StaanMCPError,
    StaanRateLimitError,
    StaanResponseError,
    StaanServerError,
    StaanTransportError,
    StaanValidationError,
    error_from_response,
)

Endpoint = Literal["search", "answer"]

# Real bodies observed against https://api.staan.ai/v2 (docs/research/error-probe.md).
BODY_401_SHORT = b'{"message":"Unauthorized","statusCode":401}'
BODY_401_GATEWAY = b'{"code":"INVALID_CREDENTIALS","message":"Unknown API key or invalid secret"}'
BODY_403_FORBIDDEN = b'{"message":"Forbidden","statusCode":403}'
BODY_403_DISABLED = b'{"code":"KEY_DISABLED","message":"API key is disabled"}'
BODY_400_LIST = (
    b'{"message":["count must be equal to 10","offset must be a factor of 10"],'
    b'"error":"Bad Request","statusCode":400}'
)
BODY_400_STRING = (
    b'{"message":"Expected property name or \'}\' in JSON at position 1 (line 1 column 2)",'
    b'"error":"Bad Request","statusCode":400}'
)
BODY_404 = b'{"message":"Cannot GET /v2/answer","error":"Not Found","statusCode":404}'


def test_message_cap_is_500_characters() -> None:
    assert MAX_UPSTREAM_MESSAGE_LENGTH == 500
    body = json.dumps({"message": "z" * 501}).encode()
    echoed = str(error_from_response(400, body, endpoint="search"))
    assert len(echoed.removeprefix("Staan rejected the request: ")) == 500


def test_hierarchy() -> None:
    assert issubclass(StaanMCPError, Exception)
    assert issubclass(ConfigurationError, StaanMCPError)
    assert issubclass(StaanAPIError, StaanMCPError)
    api_errors = (
        StaanAuthenticationError,
        StaanKeyDisabledError,
        StaanForbiddenError,
        StaanValidationError,
        StaanRateLimitError,
        StaanServerError,
        StaanTransportError,
        StaanResponseError,
    )
    for cls in api_errors:
        assert issubclass(cls, StaanAPIError)
    assert not issubclass(ConfigurationError, StaanAPIError)


def test_fatal_errors_are_exactly_the_auth_family() -> None:
    assert set(FATAL_ERRORS) == {
        StaanAuthenticationError,
        StaanKeyDisabledError,
        StaanForbiddenError,
    }


def test_api_error_defaults() -> None:
    err = StaanAPIError("boom")
    assert str(err) == "boom"
    assert err.status_code is None
    assert err.code is None
    assert err.endpoint == ""


def test_api_error_attributes() -> None:
    err = StaanTransportError("no route", status_code=599, code="X", endpoint="search")
    assert (err.status_code, err.code, err.endpoint) == (599, "X", "search")


def test_all_names_are_exported() -> None:
    for name in errors.__all__:
        assert hasattr(errors, name)


@pytest.mark.parametrize("endpoint", ["search", "answer"])
@pytest.mark.parametrize("body", [BODY_401_SHORT, BODY_401_GATEWAY])
def test_401_both_body_shapes(body: bytes, endpoint: Endpoint) -> None:
    err = error_from_response(401, body, endpoint=endpoint)
    assert type(err) is StaanAuthenticationError
    assert err.status_code == 401
    assert err.endpoint == endpoint
    assert str(err).startswith(
        "Staan authentication failed (HTTP 401). "
        "Check that STAAN_API_KEY is set to a valid, enabled key."
    )


def test_401_gateway_keeps_code_and_upstream_message() -> None:
    err = error_from_response(401, BODY_401_GATEWAY, endpoint="search")
    assert err.code == "INVALID_CREDENTIALS"
    assert str(err).endswith("Staan said: Unknown API key or invalid secret")


def test_401_short_body_has_no_code() -> None:
    err = error_from_response(401, BODY_401_SHORT, endpoint="search")
    assert err.code is None


def test_403_key_disabled() -> None:
    err = error_from_response(403, BODY_403_DISABLED, endpoint="search")
    assert type(err) is StaanKeyDisabledError
    assert err.code == "KEY_DISABLED"
    assert err.status_code == 403
    assert str(err) == (
        "This Staan API key is disabled. Enable it in the Staan console or use another key."
    )


def test_403_key_disabled_on_answer_is_still_disabled() -> None:
    err = error_from_response(403, BODY_403_DISABLED, endpoint="answer")
    assert type(err) is StaanKeyDisabledError


def test_403_answer_not_allowlisted() -> None:
    err = error_from_response(403, BODY_403_FORBIDDEN, endpoint="answer")
    assert type(err) is StaanForbiddenError
    assert err.code is None
    assert err.endpoint == "answer"
    assert str(err) == (
        "The Answer API is not enabled for this API key (invitation-only, "
        "contact support@staan.ai), or Staan has temporarily disabled it "
        "(HTTP 403 Forbidden). Web search is not affected."
    )


def test_403_search_forbidden() -> None:
    err = error_from_response(403, BODY_403_FORBIDDEN, endpoint="search")
    assert type(err) is StaanForbiddenError
    assert str(err) == (
        "Staan refused the request (HTTP 403 Forbidden): the API key may lack access to "
        "this endpoint. Check the key in the Staan console. Staan said: Forbidden"
    )


@pytest.mark.parametrize("status", [400, 422])
def test_validation_joins_list_messages(status: int) -> None:
    err = error_from_response(status, BODY_400_LIST, endpoint="search")
    assert type(err) is StaanValidationError
    assert err.status_code == status
    assert str(err) == (
        "Staan rejected the request: count must be equal to 10; offset must be a factor of 10"
    )


def test_validation_with_string_message() -> None:
    err = error_from_response(400, BODY_400_STRING, endpoint="search")
    assert type(err) is StaanValidationError
    assert str(err) == (
        "Staan rejected the request: Expected property name or '}' in JSON at position 1 "
        "(line 1 column 2)"
    )


@pytest.mark.parametrize("status", [400, 422])
def test_validation_without_message(status: int) -> None:
    err = error_from_response(status, b"", endpoint="answer")
    assert type(err) is StaanValidationError
    assert str(err) == f"Staan rejected the request (HTTP {status})."


def test_429() -> None:
    err = error_from_response(429, b"", endpoint="search")
    assert type(err) is StaanRateLimitError
    assert err.status_code == 429
    assert str(err) == "Staan rate limit reached (HTTP 429). Try again shortly."


def test_429_with_message() -> None:
    err = error_from_response(429, b'{"message":"Too many requests"}', endpoint="search")
    assert str(err) == (
        "Staan rate limit reached (HTTP 429). Try again shortly. Staan said: Too many requests"
    )


@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
def test_5xx_is_server_error_without_echoing_the_body(status: int) -> None:
    err = error_from_response(status, b'{"message":"secret internals"}', endpoint="search")
    assert type(err) is StaanServerError
    assert err.status_code == status
    assert str(err) == f"Staan is temporarily unavailable (HTTP {status}). Try again later."


def test_4xx_boundary_is_not_a_server_outage() -> None:
    err = error_from_response(499, b"", endpoint="search")
    assert type(err) is StaanServerError
    assert str(err) == "Staan returned an unexpected response (HTTP 499)."
    assert "unavailable" not in str(err)


def test_500_boundary_is_a_server_outage() -> None:
    assert "unavailable" in str(error_from_response(500, b"", endpoint="search"))


def test_404_with_empty_body() -> None:
    err = error_from_response(404, b"", endpoint="answer")
    assert type(err) is StaanServerError
    assert err.status_code == 404
    assert str(err) == "Staan returned an unexpected response (HTTP 404)."


def test_404_with_nest_body_echoes_message() -> None:
    err = error_from_response(404, BODY_404, endpoint="answer")
    assert type(err) is StaanServerError
    assert str(err) == (
        "Staan returned an unexpected response (HTTP 404). Staan said: Cannot GET /v2/answer"
    )


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json at all",
        b"\xff\xfe\x00 not utf-8",
        b"[1, 2, 3]",
        b'"just a string"',
        b"null",
        b"{}",
        b'{"message": 42}',
        b'{"message": null}',
        b'{"message": []}',
        b'{"message": ["", "   ", 5, null]}',
        b"[" * 200_000,
    ],
    ids=[
        "empty",
        "not-json",
        "not-utf8",
        "array",
        "string",
        "null",
        "empty-object",
        "number-message",
        "null-message",
        "empty-list-message",
        "blank-list-message",
        "deeply-nested",
    ],
)
def test_unusable_bodies_yield_message_free_errors(body: bytes) -> None:
    err = error_from_response(401, body, endpoint="search")
    assert type(err) is StaanAuthenticationError
    assert err.code is None
    assert "Staan said" not in str(err)


@pytest.mark.parametrize(
    "payload",
    [{"code": ""}, {"code": 7}, {"code": None}, {"code": ["KEY_DISABLED"]}],
)
def test_non_string_or_empty_code_is_ignored(payload: dict[str, object]) -> None:
    err = error_from_response(403, json.dumps(payload).encode(), endpoint="search")
    assert type(err) is StaanForbiddenError
    assert err.code is None


def test_list_message_skips_non_strings_and_blanks() -> None:
    body = json.dumps({"message": ["  a  ", "", 3, None, "b"]}).encode()
    err = error_from_response(400, body, endpoint="search")
    assert str(err) == "Staan rejected the request: a; b"


def test_string_message_is_stripped() -> None:
    body = json.dumps({"message": "  padded \n"}).encode()
    err = error_from_response(400, body, endpoint="search")
    assert str(err) == "Staan rejected the request: padded"


def test_message_at_the_cap_is_kept_whole() -> None:
    text = "x" * MAX_UPSTREAM_MESSAGE_LENGTH
    err = error_from_response(400, json.dumps({"message": text}).encode(), endpoint="search")
    assert str(err) == f"Staan rejected the request: {text}"


def test_message_over_the_cap_is_truncated_with_ellipsis() -> None:
    text = "y" * (MAX_UPSTREAM_MESSAGE_LENGTH + 1)
    err = error_from_response(400, json.dumps({"message": text}).encode(), endpoint="search")
    echoed = str(err).removeprefix("Staan rejected the request: ")
    assert len(echoed) == MAX_UPSTREAM_MESSAGE_LENGTH
    assert echoed == "y" * (MAX_UPSTREAM_MESSAGE_LENGTH - 3) + "..."


def test_long_list_message_is_capped_after_joining() -> None:
    body = json.dumps({"message": ["a" * 300, "b" * 300]}).encode()
    err = error_from_response(400, body, endpoint="search")
    echoed = str(err).removeprefix("Staan rejected the request: ")
    assert len(echoed) == MAX_UPSTREAM_MESSAGE_LENGTH
    assert echoed.endswith("...")


def test_raw_body_beyond_message_is_never_echoed() -> None:
    body = json.dumps(
        {"message": "bad", "error": "Bad Request", "statusCode": 400, "debug": "TOPSECRET"}
    ).encode()
    assert "TOPSECRET" not in str(error_from_response(400, body, endpoint="search"))


@given(
    status=st.integers(min_value=100, max_value=599),
    body=st.binary(max_size=200),
    endpoint=st.sampled_from(["search", "answer"]),
)
def test_never_raises_and_keeps_status(status: int, body: bytes, endpoint: Endpoint) -> None:
    err = error_from_response(status, body, endpoint=endpoint)
    assert isinstance(err, StaanAPIError)
    assert err.status_code == status
    assert err.endpoint == endpoint
    assert str(err)


@given(message=st.text(max_size=2000))
def test_echoed_message_never_exceeds_the_cap(message: str) -> None:
    body = json.dumps({"message": message}).encode()
    err = error_from_response(400, body, endpoint="search")
    echoed = str(err).removeprefix("Staan rejected the request")
    assert len(echoed.removeprefix(": ")) <= MAX_UPSTREAM_MESSAGE_LENGTH + len(" (HTTP 400).")
