"""Tests for the OAuth resource-server gate (JWT/JWKS and introspection verifiers)."""
# pylint: disable=redefined-outer-name,missing-function-docstring,too-few-public-methods,too-many-lines,protected-access,unbalanced-tuple-unpacking,use-implicit-booleaness-not-comparison

from __future__ import annotations

import base64
import builtins
import contextlib
import io
import json
import logging
import sys
import time
import warnings
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from mcp.server.auth.provider import AccessToken, principal_components
from pydantic import AnyHttpUrl

from staan_mcp.auth import (
    INTROSPECTION_TIMEOUT,
    JWKS_COOLDOWN,
    JWKS_LIFESPAN,
    JWKS_TIMEOUT,
    MAX_INTROSPECTED_TOKEN_LENGTH,
    IntrospectionTokenVerifier,
    JWTTokenVerifier,
    OAuthDependencyError,
    _equivalent_urls,
    _expiry,
    build_auth_settings,
    build_token_verifier,
)
from staan_mcp.config import AuthConfig
from staan_mcp.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable

ISSUER = "https://auth.example.com"
RESOURCE = "https://mcp.example.com/mcp"
JWKS_URL = "https://auth.example.com/jwks.json"
INTROSPECTION_URL = "https://auth.example.com/introspect"
CLIENT_ID = "mcp-server"
CLIENT_SECRET = "s3cr3t:with+chars"
HS_SECRET = b"x" * 64

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------------------
# Helpers and fixtures
# --------------------------------------------------------------------------------------


def jwt_config(**overrides: Any) -> AuthConfig:
    """Return a JWT-flavoured config."""
    values: dict[str, Any] = {"issuer_url": ISSUER, "resource_url": RESOURCE, "jwks_url": JWKS_URL}
    return AuthConfig(**{**values, **overrides})


def introspection_config(**overrides: Any) -> AuthConfig:
    """Return an introspection-flavoured config."""
    values: dict[str, Any] = {
        "issuer_url": ISSUER,
        "resource_url": RESOURCE,
        "introspection_url": INTROSPECTION_URL,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    return AuthConfig(**{**values, **overrides})


class FakeJWKClient:
    """Stand-in for ``jwt.PyJWKClient`` that returns a fixed key or raises."""

    def __init__(self, key: Any = None, error: Exception | None = None) -> None:
        self.key = key
        self.error = error
        self.tokens: list[str] = []

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(key=self.key)


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def claims(**overrides: Any) -> dict[str, Any]:
    """Return valid JWT claims, with ``overrides`` applied (``None`` removes a claim)."""
    base: dict[str, Any] = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "exp": int(time.time()) + 600,
        "sub": "user-1",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def mint(key: Any, payload: dict[str, Any], algorithm: str = "RS256") -> str:
    """Sign ``payload`` with ``key``."""
    return jwt.encode(payload, key, algorithm=algorithm)


def rsa_verifier(rsa_key: rsa.RSAPrivateKey, **config: Any) -> JWTTokenVerifier:
    return JWTTokenVerifier(jwt_config(**config), jwk_client=FakeJWKClient(rsa_key.public_key()))


# --------------------------------------------------------------------------------------
# JWTTokenVerifier
# --------------------------------------------------------------------------------------


async def test_jwt_good_token(rsa_key: rsa.RSAPrivateKey) -> None:
    exp = int(time.time()) + 600
    token = mint(rsa_key, claims(exp=exp, scope="staan:search staan:answer", client_id="cli"))
    jwk_client = FakeJWKClient(rsa_key.public_key())
    verifier = JWTTokenVerifier(jwt_config(), jwk_client=jwk_client)

    result = await verifier.verify_token(token)

    assert result == AccessToken(
        token=token,
        client_id="cli",
        scopes=["staan:search", "staan:answer"],
        expires_at=exp,
        resource=RESOURCE,
        subject="user-1",
        claims={"iss": ISSUER},
    )
    assert jwk_client.tokens == [token]


async def test_jwt_good_token_ec(ec_key: ec.EllipticCurvePrivateKey) -> None:
    token = mint(ec_key, claims(), algorithm="ES256")
    verifier = JWTTokenVerifier(jwt_config(), jwk_client=FakeJWKClient(ec_key.public_key()))

    result = await verifier.verify_token(token)

    assert result is not None
    assert result.client_id == "user-1"
    assert result.resource == RESOURCE


async def test_jwt_audience_list_containing_resource(rsa_key: rsa.RSAPrivateKey) -> None:
    token = mint(rsa_key, claims(aud=["https://other.example", RESOURCE]))

    assert await rsa_verifier(rsa_key).verify_token(token) is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://other.example/mcp"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 60},
        {"exp": None},
        {"iss": None},
        {"aud": None},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "missing-exp", "missing-iss", "missing-aud"],
)
async def test_jwt_bad_claims_rejected(
    rsa_key: rsa.RSAPrivateKey, overrides: dict[str, Any]
) -> None:
    token = mint(rsa_key, claims(**overrides))

    assert await rsa_verifier(rsa_key).verify_token(token) is None


async def test_jwt_signed_by_other_key_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    assert await rsa_verifier(rsa_key).verify_token(mint(other, claims())) is None


async def test_jwt_alg_none_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    token = jwt.encode(claims(), "", algorithm="none")

    assert await rsa_verifier(rsa_key).verify_token(token) is None


async def test_jwt_hs256_signed_with_public_key_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    token = jwt.encode(claims(), HS_SECRET, algorithm="HS256")
    verifier = JWTTokenVerifier(jwt_config(), jwk_client=FakeJWKClient(HS_SECRET))

    assert await verifier.verify_token(token) is None
    assert await rsa_verifier(rsa_key).verify_token(token) is None


async def test_jwt_algorithm_outside_allowlist_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    token = mint(rsa_key, claims())

    verifier = rsa_verifier(rsa_key, algorithms=("ES256",))

    assert await verifier.verify_token(token) is None


async def test_jwt_algorithm_allowlist_is_taken_from_config(
    ec_key: ec.EllipticCurvePrivateKey,
) -> None:
    token = mint(ec_key, claims(), algorithm="ES256")
    verifier = JWTTokenVerifier(
        jwt_config(algorithms=("RS256",)), jwk_client=FakeJWKClient(ec_key.public_key())
    )

    assert await verifier.verify_token(token) is None


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "x" * 200])
async def test_jwt_garbage_token_rejected(rsa_key: rsa.RSAPrivateKey, token: str) -> None:
    verifier = JWTTokenVerifier(jwt_config(), jwk_client=FakeJWKClient(rsa_key.public_key()))

    assert await verifier.verify_token(token) is None


@pytest.mark.parametrize(
    "error",
    [jwt.PyJWKClientError("no key"), OSError("network down"), RuntimeError("boom")],
    ids=["jwk-error", "os-error", "unexpected"],
)
async def test_jwt_key_lookup_failure_rejected(
    rsa_key: rsa.RSAPrivateKey, error: Exception
) -> None:
    token = mint(rsa_key, claims())
    verifier = JWTTokenVerifier(jwt_config(), jwk_client=FakeJWKClient(error=error))

    assert await verifier.verify_token(token) is None


async def test_jwt_scopes_from_scope_string(rsa_key: rsa.RSAPrivateKey) -> None:
    token = mint(rsa_key, claims(scope="  a   b c "))

    result = await rsa_verifier(rsa_key).verify_token(token)

    assert result is not None
    assert result.scopes == ["a", "b", "c"]


async def test_jwt_scopes_from_scp_list(rsa_key: rsa.RSAPrivateKey) -> None:
    token = mint(rsa_key, claims(scp=["a", "b"]))

    result = await rsa_verifier(rsa_key).verify_token(token)

    assert result is not None
    assert result.scopes == ["a", "b"]


async def test_jwt_no_scopes(rsa_key: rsa.RSAPrivateKey) -> None:
    result = await rsa_verifier(rsa_key).verify_token(mint(rsa_key, claims()))

    assert result is not None
    assert result.scopes == []


async def test_jwt_logs_rejection_without_token(
    rsa_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    token = mint(rsa_key, claims(aud="https://other.example/mcp"))
    caplog.set_level(logging.DEBUG, logger="staan_mcp.auth")

    await rsa_verifier(rsa_key).verify_token(token)

    assert [r.getMessage() for r in caplog.records if r.name == "staan_mcp.auth"] == [
        "JWT rejected (InvalidAudienceError)"
    ]
    assert token not in caplog.text


async def test_jwt_end_to_end_with_real_pyjwk_client(
    rsa_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    public_jwk["kid"] = "key-1"
    fetched: list[str] = []

    def fake_fetch(client: jwt.PyJWKClient) -> dict[str, Any]:
        fetched.append(client.uri)
        return {"keys": [public_jwk]}

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch)
    token = jwt.encode(claims(), rsa_key, algorithm="RS256", headers={"kid": "key-1"})
    verifier = JWTTokenVerifier(jwt_config())

    result = await verifier.verify_token(token)

    assert result is not None
    assert fetched == [JWKS_URL]


def test_jwt_default_client_uses_configured_jwks_url() -> None:
    verifier = JWTTokenVerifier(jwt_config())

    assert isinstance(verifier._jwk_client, jwt.PyJWKClient)
    assert verifier._jwk_client.uri == JWKS_URL


def test_jwt_without_jwks_url_or_client_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        JWTTokenVerifier(jwt_config(jwks_url=None))

    assert str(excinfo.value) == "JWT verification needs a JWKS URL (STAAN_MCP_AUTH_JWKS_URL)."


def test_jwt_missing_pyjwt_raises_dependency_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "jwt", None)

    with pytest.raises(OAuthDependencyError) as excinfo:
        JWTTokenVerifier(jwt_config())

    assert str(excinfo.value) == (
        "JWT verification needs PyJWT, which is not installed. "
        "Install it with: pip install 'staan-mcp[oauth]'"
    )
    assert isinstance(excinfo.value, ConfigurationError)
    assert isinstance(excinfo.value.__cause__, ImportError)


def test_jwt_missing_pyjwt_via_import_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "jwt":
            msg = "No module named 'jwt'"
            raise ModuleNotFoundError(msg, name="jwt")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "jwt")
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(OAuthDependencyError):
        JWTTokenVerifier(jwt_config(), jwk_client=FakeJWKClient())


# --------------------------------------------------------------------------------------
# IntrospectionTokenVerifier
# --------------------------------------------------------------------------------------


class Recorder:
    """Mock transport that answers every request with ``handler`` and records the requests."""

    def __init__(self, handler: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.requests: list[httpx2.Request] = []
        self._handler = handler
        self.transport = httpx2.MockTransport(self._record)

    def _record(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._handler(request)


def answering(payload: Any, status: int = 200) -> Recorder:
    return Recorder(lambda _request: httpx2.Response(status, json=payload))


def active(**overrides: Any) -> dict[str, Any]:
    """Return an active introspection response, with ``overrides`` applied (``None`` removes)."""
    base: dict[str, Any] = {"active": True, "aud": RESOURCE, "exp": int(time.time()) + 600}
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def introspector(recorder: Recorder, **config: Any) -> IntrospectionTokenVerifier:
    return IntrospectionTokenVerifier(introspection_config(**config), transport=recorder.transport)


async def test_introspection_good_token() -> None:
    exp = int(time.time()) + 600
    recorder = answering(
        active(exp=exp, scope="staan:search", client_id="cli", iss=ISSUER, sub="user-1")
    )

    result = await introspector(recorder).verify_token("opaque-token")

    assert result == AccessToken(
        token="opaque-token",
        client_id="cli",
        scopes=["staan:search"],
        expires_at=exp,
        resource=RESOURCE,
        subject="user-1",
        claims={"iss": ISSUER},
    )


async def test_introspection_request_shape() -> None:
    recorder = answering(active())

    await introspector(recorder).verify_token("tok&en=1")

    (request,) = recorder.requests
    expected_basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert request.method == "POST"
    assert str(request.url) == INTROSPECTION_URL
    assert request.headers["authorization"] == f"Basic {expected_basic}"
    assert request.headers["accept"] == "application/json"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.content.decode()) == {"token": ["tok&en=1"]}


async def test_introspection_client_uses_timeout() -> None:
    recorder = answering(active())

    await introspector(recorder).verify_token("t")

    (request,) = recorder.requests
    assert request.extensions["timeout"] == {
        "connect": INTROSPECTION_TIMEOUT,
        "read": INTROSPECTION_TIMEOUT,
        "write": INTROSPECTION_TIMEOUT,
        "pool": INTROSPECTION_TIMEOUT,
    }


async def test_introspection_does_not_follow_redirects() -> None:
    recorder = Recorder(lambda _r: httpx2.Response(302, headers={"location": "https://x.example/"}))

    assert await introspector(recorder).verify_token("t") is None
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("active_value", [False, None, "true", 1])
async def test_introspection_inactive_rejected(active_value: Any) -> None:
    recorder = answering(active(active=active_value))

    assert await introspector(recorder).verify_token("t") is None


@pytest.mark.parametrize(
    ("offset", "verdict"), [(-1, "rejected"), (0, "rejected"), (1, "accepted")]
)
async def test_introspection_expiry_must_lie_strictly_in_the_future(
    monkeypatch: pytest.MonkeyPatch, offset: int, verdict: str
) -> None:
    now = 2_000_000_000
    monkeypatch.setattr(time, "time", lambda: float(now))
    recorder = answering(active(exp=now + offset))

    result = await introspector(recorder).verify_token("t")

    assert ("accepted" if result is not None else "rejected") == verdict


async def test_introspection_audience_string() -> None:
    assert await introspector(answering(active(aud=RESOURCE))).verify_token("t") is not None


async def test_introspection_audience_list() -> None:
    recorder = answering(active(aud=["https://other.example", RESOURCE]))

    assert await introspector(recorder).verify_token("t") is not None


@pytest.mark.parametrize(
    "aud",
    [None, "https://other.example/mcp", ["https://other.example/mcp"], [], 42, {"a": RESOURCE}],
    ids=["missing", "str-mismatch", "list-mismatch", "empty-list", "number", "object"],
)
async def test_introspection_audience_rejected(aud: Any) -> None:
    assert await introspector(answering(active(aud=aud))).verify_token("t") is None


async def test_introspection_expired_rejected() -> None:
    recorder = answering(active(exp=int(time.time()) - 1))

    assert await introspector(recorder).verify_token("t") is None


@pytest.mark.parametrize("exp", ["soon", True, None, [1]])
async def test_introspection_unusable_exp_rejected(exp: Any) -> None:
    payload = active()
    payload["exp"] = exp

    assert await introspector(answering(payload)).verify_token("t") is None


async def test_introspection_missing_exp_accepted() -> None:
    result = await introspector(answering(active(exp=None))).verify_token("t")

    assert result is not None
    assert result.expires_at is None


async def test_introspection_float_exp_is_truncated() -> None:
    exp = time.time() + 600.7
    result = await introspector(answering(active(exp=exp))).verify_token("t")

    assert result is not None
    assert result.expires_at == int(exp)


async def test_introspection_issuer_mismatch_rejected() -> None:
    recorder = answering(active(iss="https://evil.example.com"))

    assert await introspector(recorder).verify_token("t") is None


async def test_introspection_matching_issuer_accepted() -> None:
    assert await introspector(answering(active(iss=ISSUER))).verify_token("t") is not None


@pytest.mark.parametrize("status", [201, 204, 400, 401, 404, 500])
async def test_introspection_non_200_rejected(status: int) -> None:
    recorder = answering(active(), status=status)

    assert await introspector(recorder).verify_token("t") is None


@pytest.mark.parametrize("payload", [[], "active", 3, None])
async def test_introspection_non_object_json_rejected(payload: Any) -> None:
    assert await introspector(answering(payload)).verify_token("t") is None


async def test_introspection_invalid_json_rejected() -> None:
    recorder = Recorder(lambda _r: httpx2.Response(200, content=b"<html>not json</html>"))

    assert await introspector(recorder).verify_token("t") is None


@pytest.mark.parametrize(
    "error",
    [
        httpx2.ConnectError("refused"),
        httpx2.ReadTimeout("slow"),
        RuntimeError("unexpected"),
    ],
)
async def test_introspection_network_error_rejected(error: Exception) -> None:
    def fail(_request: httpx2.Request) -> httpx2.Response:
        raise error

    assert await introspector(Recorder(fail)).verify_token("t") is None


async def test_introspection_logs_without_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="staan_mcp.auth")
    secret_token = "very-secret-token"

    await introspector(answering(active(active=False))).verify_token(secret_token)
    await introspector(answering({}, status=500)).verify_token(secret_token)
    await introspector(answering(active(aud="x"))).verify_token(secret_token)
    boom = Recorder(lambda _r: (_ for _ in ()).throw(httpx2.ConnectError("refused")))
    await introspector(boom).verify_token(secret_token)

    assert [r.getMessage() for r in caplog.records if r.name == "staan_mcp.auth"] == [
        "Introspection reports the token as inactive",
        "Introspection endpoint answered HTTP 500",
        "Introspection response failed the audience, expiry or issuer check",
        "Introspection failed (ConnectError)",
    ]
    assert secret_token not in caplog.text


@pytest.mark.parametrize(
    ("claims_in", "expected"),
    [
        ({"client_id": "c", "azp": "a", "sub": "s"}, "c"),
        ({"azp": "a", "sub": "s"}, "a"),
        ({"sub": "s"}, "s"),
        ({}, "unknown"),
        ({"client_id": "", "azp": "", "sub": ""}, "unknown"),
        ({"client_id": 5, "azp": None, "sub": "s"}, "s"),
    ],
)
async def test_introspection_client_id_fallback_order(
    claims_in: dict[str, Any], expected: str
) -> None:
    result = await introspector(answering(active(**claims_in))).verify_token("t")

    assert result is not None
    assert result.client_id == expected


@pytest.mark.parametrize(
    ("claims_in", "expected"),
    [
        ({"scope": "a b", "scp": ["c"]}, ["a", "b"]),
        ({"scp": ["a", "b"]}, ["a", "b"]),
        ({"scp": "a b"}, ["a", "b"]),
        ({"scp": ["a", 3, "b"]}, ["a", "b"]),
        ({"scope": 7, "scp": ["a"]}, ["a"]),
        ({"scope": ""}, []),
        ({"scp": 7}, []),
        ({}, []),
    ],
)
async def test_introspection_scope_extraction(
    claims_in: dict[str, Any], expected: list[str]
) -> None:
    result = await introspector(answering(active(**claims_in))).verify_token("t")

    assert result is not None
    assert result.scopes == expected


async def test_introspection_resource_is_always_the_configured_resource() -> None:
    result = await introspector(answering(active(aud=["z", RESOURCE]))).verify_token("t")

    assert result is not None
    assert result.resource == RESOURCE


@pytest.mark.parametrize(
    "overrides",
    [
        {"introspection_url": None},
        {"client_id": None},
        {"client_secret": None},
    ],
)
def test_introspection_requires_endpoint_and_credentials(overrides: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        IntrospectionTokenVerifier(introspection_config(**overrides))

    assert str(excinfo.value) == (
        "Token introspection needs an endpoint URL, a client id and a client secret."
    )


# --------------------------------------------------------------------------------------
# Factories
# --------------------------------------------------------------------------------------


def test_build_token_verifier_picks_jwt_for_jwks_url() -> None:
    verifier = build_token_verifier(jwt_config())

    assert isinstance(verifier, JWTTokenVerifier)


def test_build_token_verifier_picks_introspection() -> None:
    verifier = build_token_verifier(introspection_config())

    assert isinstance(verifier, IntrospectionTokenVerifier)


@pytest.mark.parametrize(
    "config",
    [
        AuthConfig(issuer_url=ISSUER, resource_url=RESOURCE),
        AuthConfig(
            issuer_url=ISSUER,
            resource_url=RESOURCE,
            jwks_url=JWKS_URL,
            introspection_url=INTROSPECTION_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        ),
    ],
    ids=["neither", "both"],
)
def test_build_token_verifier_requires_exactly_one_source(config: AuthConfig) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        build_token_verifier(config)

    assert str(excinfo.value) == "Set exactly one of the JWKS URL and the introspection URL."


def test_build_token_verifier_propagates_missing_pyjwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "jwt", None)

    with pytest.raises(OAuthDependencyError):
        build_token_verifier(jwt_config())


def test_build_auth_settings_fields() -> None:
    config = jwt_config(required_scopes=("staan:search", "staan:answer"))

    settings = build_auth_settings(config)

    assert settings.issuer_url == AnyHttpUrl(ISSUER)
    assert settings.resource_server_url == AnyHttpUrl(RESOURCE)
    assert settings.required_scopes == ["staan:search", "staan:answer"]
    assert settings.validate_token_resource is True


def test_build_auth_settings_without_scopes() -> None:
    assert build_auth_settings(jwt_config()).required_scopes == []


def test_build_auth_settings_emits_no_deprecation_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_auth_settings(jwt_config())


# --------------------------------------------------------------------------------------
# Hardening: non-finite exp, JWKS amplification, principal, origin-only URLs, token limits
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_expiry_of_a_non_finite_float_is_none(value: float) -> None:
    assert _expiry(value) is None


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
async def test_introspection_with_a_non_finite_exp_is_rejected_not_raised(literal: str) -> None:
    # json.loads accepts these literals, and int(inf) raises OverflowError: reject the token.
    body = f'{{"active": true, "aud": "{RESOURCE}", "exp": {literal}}}'.encode()
    recorder = Recorder(lambda _request: httpx2.Response(200, content=body))

    assert await introspector(recorder).verify_token("opaque") is None
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("exp", [float("inf"), float("nan")])
async def test_jwt_with_a_non_finite_exp_is_rejected_not_raised(
    rsa_key: rsa.RSAPrivateKey, exp: float
) -> None:
    token = mint(rsa_key, claims(exp=exp))

    assert await rsa_verifier(rsa_key).verify_token(token) is None


class CountingOpener:
    """Stand-in for the ``urllib`` opener PyJWKClient uses; counts JWKS fetches."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        self.timeouts: list[float] = []
        self._body = json.dumps(jwks).encode()

    def open(self, _request: object, timeout: float) -> Any:
        self.timeouts.append(timeout)
        return contextlib.closing(io.BytesIO(self._body))


async def test_forged_tokens_with_unknown_kids_cannot_make_the_jwks_endpoint_hammered(
    rsa_key: rsa.RSAPrivateKey, ec_key: ec.EllipticCurvePrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    public_jwk["kid"] = "real-key"
    opener = CountingOpener({"keys": [public_jwk]})
    monkeypatch.setattr("jwt.jwks_client.urllib.request.build_opener", lambda *_a, **_k: opener)
    verifier = JWTTokenVerifier(jwt_config())

    for index in range(50):
        forged = jwt.encode(claims(), ec_key, algorithm="ES256", headers={"kid": f"forged-{index}"})
        assert await verifier.verify_token(forged) is None
    genuine = jwt.encode(claims(), rsa_key, algorithm="RS256", headers={"kid": "real-key"})

    assert await verifier.verify_token(genuine) is not None
    # One fetch for the first token; the cooldown stops the 49 unknown kids from forcing more.
    assert opener.timeouts == [JWKS_TIMEOUT]


def test_jwt_default_client_is_built_with_explicit_timeouts() -> None:
    client = JWTTokenVerifier(jwt_config())._jwk_client

    assert isinstance(client, jwt.PyJWKClient)
    assert client.timeout == JWKS_TIMEOUT == 10.0
    assert client.cooldown_duration == JWKS_COOLDOWN == 30.0
    assert client.jwk_set_cache is not None
    assert client.jwk_set_cache.lifespan == JWKS_LIFESPAN == 300.0


@pytest.mark.parametrize(
    ("claims_in", "subject", "issuer"),
    [
        ({"sub": "alice", "iss": ISSUER}, "alice", ISSUER),
        ({"iss": ISSUER}, None, ISSUER),
        ({"sub": "", "iss": ISSUER}, None, ISSUER),
        ({"sub": 42, "iss": ISSUER}, None, ISSUER),
        ({"sub": ["a"]}, None, ISSUER),
    ],
)
async def test_introspection_access_token_carries_subject_and_issuer(
    claims_in: dict[str, Any], subject: str | None, issuer: str
) -> None:
    result = await introspector(answering(active(**claims_in))).verify_token("t")

    assert result is not None
    assert result.subject == subject
    assert result.claims == {"iss": issuer}


async def test_introspection_without_iss_falls_back_to_the_configured_issuer() -> None:
    verifier = introspector(answering(active(sub="u")), issuer_url="https://other.example/")

    result = await verifier.verify_token("t")

    assert result is not None
    assert result.claims == {"iss": "https://other.example/"}


async def test_jwt_access_token_carries_subject_and_issuer(rsa_key: rsa.RSAPrivateKey) -> None:
    token = mint(rsa_key, claims(sub="bob"))

    result = await rsa_verifier(rsa_key).verify_token(token)

    assert result is not None
    assert result.subject == "bob"
    assert result.claims == {"iss": ISSUER}
    assert principal_components(result) == ("bob", ISSUER, "bob")


async def test_two_users_of_one_client_are_different_principals(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    verifier = rsa_verifier(rsa_key)
    alice = await verifier.verify_token(mint(rsa_key, claims(sub="alice", azp="app")))
    bob = await verifier.verify_token(mint(rsa_key, claims(sub="bob", azp="app")))

    assert alice is not None
    assert bob is not None
    assert alice.client_id == bob.client_id == "app"
    assert principal_components(alice) != principal_components(bob)


def test_equivalent_urls_accept_both_spellings_of_an_origin() -> None:
    assert _equivalent_urls("https://mcp.example.com") == {
        "https://mcp.example.com",
        "https://mcp.example.com/",
    }
    assert _equivalent_urls("https://mcp.example.com/") == {
        "https://mcp.example.com",
        "https://mcp.example.com/",
    }
    assert _equivalent_urls("http://127.0.0.1:8000") == {
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8000/",
    }


def test_equivalent_urls_match_a_url_with_a_path_exactly() -> None:
    assert _equivalent_urls("https://mcp.example.com/mcp") == {"https://mcp.example.com/mcp"}


ORIGIN_RESOURCE = "https://mcp.example.com"
ORIGIN_ISSUER = "https://auth.example.com"


@pytest.mark.parametrize("aud", [ORIGIN_RESOURCE, f"{ORIGIN_RESOURCE}/"])
@pytest.mark.parametrize("iss", [ORIGIN_ISSUER, f"{ORIGIN_ISSUER}/"])
@pytest.mark.parametrize("configured", ["bare", "slash"])
async def test_jwt_origin_only_urls_match_with_or_without_the_slash(
    rsa_key: rsa.RSAPrivateKey, aud: str, iss: str, configured: str
) -> None:
    suffix = "/" if configured == "slash" else ""
    verifier = rsa_verifier(
        rsa_key, resource_url=ORIGIN_RESOURCE + suffix, issuer_url=ORIGIN_ISSUER + suffix
    )

    result = await verifier.verify_token(mint(rsa_key, claims(aud=aud, iss=iss)))

    assert result is not None
    assert result.resource == ORIGIN_RESOURCE + suffix


async def test_jwt_audience_list_with_the_slashed_origin_is_accepted(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    verifier = rsa_verifier(rsa_key, resource_url=ORIGIN_RESOURCE, issuer_url=ORIGIN_ISSUER)

    token = mint(rsa_key, claims(aud=["other", f"{ORIGIN_RESOURCE}/"], iss=ORIGIN_ISSUER))

    assert await verifier.verify_token(token) is not None


@pytest.mark.parametrize(
    ("aud", "iss"),
    [
        ("https://mcp.example.com/other", ORIGIN_ISSUER),
        ("https://mcp.example.org/", ORIGIN_ISSUER),
        (ORIGIN_RESOURCE, "https://evil.example.com/"),
        (ORIGIN_RESOURCE, f"{ORIGIN_ISSUER}//"),
    ],
)
async def test_jwt_origin_only_urls_do_not_loosen_anything_else(
    rsa_key: rsa.RSAPrivateKey, aud: str, iss: str
) -> None:
    verifier = rsa_verifier(rsa_key, resource_url=ORIGIN_RESOURCE, issuer_url=ORIGIN_ISSUER)

    assert await verifier.verify_token(mint(rsa_key, claims(aud=aud, iss=iss))) is None


@pytest.mark.parametrize("aud", ["https://mcp.example.com/mcp/", "https://mcp.example.com/MCP"])
async def test_jwt_a_resource_with_a_path_is_matched_exactly(
    rsa_key: rsa.RSAPrivateKey, aud: str
) -> None:
    assert await rsa_verifier(rsa_key).verify_token(mint(rsa_key, claims(aud=aud))) is None


@pytest.mark.parametrize("aud", [ORIGIN_RESOURCE, f"{ORIGIN_RESOURCE}/", [f"{ORIGIN_RESOURCE}/"]])
async def test_introspection_origin_only_audience_matches_both_spellings(aud: Any) -> None:
    recorder = answering(active(aud=aud, iss=f"{ORIGIN_ISSUER}/"))
    verifier = introspector(recorder, resource_url=ORIGIN_RESOURCE, issuer_url=ORIGIN_ISSUER)

    assert await verifier.verify_token("t") is not None


@pytest.mark.parametrize("aud", ["https://mcp.example.com/mcp/", ["https://mcp.example.com/x"]])
async def test_introspection_a_resource_with_a_path_is_matched_exactly(aud: Any) -> None:
    assert await introspector(answering(active(aud=aud))).verify_token("t") is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": [{"a": 1}, ["nested"]]},
        {"iss": ["https://auth.example.com"]},
        {"iss": {"a": 1}},
        {"iss": 7},
        {"aud": 7},
    ],
)
async def test_introspection_unhashable_or_odd_claims_are_rejected_not_raised(
    overrides: dict[str, Any],
) -> None:
    assert await introspector(answering(active(**overrides))).verify_token("t") is None


@pytest.mark.parametrize(
    "token",
    [
        "a" * (MAX_INTROSPECTED_TOKEN_LENGTH + 1),
        "",
        "has space",
        "tab\there",
        "new\nline",
        "nul\x00",
        "café",
        "“quoted”",
        "del\x7f",
    ],
)
async def test_introspection_rejects_implausible_tokens_without_a_request(token: str) -> None:
    recorder = answering(active())

    assert await introspector(recorder).verify_token(token) is None
    assert recorder.requests == []


@pytest.mark.parametrize(
    "token",
    ["a" * MAX_INTROSPECTED_TOKEN_LENGTH, "AbC-123._~+/=", "!", "~", "eyJhbGciOi.eyJzdWIi.c2ln"],
)
async def test_introspection_forwards_plausible_tokens(token: str) -> None:
    recorder = answering(active())

    assert await introspector(recorder).verify_token(token) is not None
    assert len(recorder.requests) == 1


async def test_introspection_logs_the_local_rejection_without_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "x y" * 3000
    with caplog.at_level(logging.DEBUG, logger="staan_mcp.auth"):
        await introspector(answering(active())).verify_token(token)

    assert [r.getMessage() for r in caplog.records if r.name == "staan_mcp.auth"] == [
        "Token rejected without introspection: unexpected length or characters"
    ]
    assert token not in caplog.text


async def test_introspection_reuses_one_client_and_closes_it() -> None:
    recorder = answering(active())
    verifier = introspector(recorder)

    for index in range(5):
        assert await verifier.verify_token(f"token-{index}") is not None
    http = verifier._http
    open_before = not http.is_closed

    await verifier.aclose()
    await verifier.aclose()  # safe to repeat

    assert open_before
    assert len(recorder.requests) == 5
    assert http.is_closed


async def test_introspection_basic_auth_is_set_once_on_the_shared_client() -> None:
    recorder = answering(active())
    verifier = introspector(recorder)

    await verifier.verify_token("one")
    await verifier.verify_token("two")

    expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert [r.headers["authorization"] for r in recorder.requests] == [expected, expected]
