"""Tests of the streamable HTTP ASGI app, driven in-process through ``httpx2.ASGITransport``.

They cover what the in-memory client cannot: the session manager lifespan, stateful versus
stateless behaviour, DNS-rebinding protection, and the optional OAuth resource-server gate.
"""
# pylint: disable=redefined-outer-name,too-few-public-methods,protected-access

from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from staan_fakes import API_KEY, FakeStaan, make_server, search_response, text_of

import staan_mcp.server as server_module
from staan_mcp.auth import IntrospectionTokenVerifier, JWTTokenVerifier
from staan_mcp.cli import _security_settings
from staan_mcp.config import AuthConfig, HttpConfig, Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from mcp.server import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

pytestmark = pytest.mark.anyio

Flavour = Literal["stateful", "stateless"]

BASE_URL = "http://127.0.0.1:8000"
MCP_URL = f"{BASE_URL}/mcp"
RESOURCE_URL = "https://mcp.example.com/mcp"
ISSUER_URL = "https://auth.example.com"
METADATA_URL = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
JSON_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


@asynccontextmanager
async def serve(
    server: MCPServer[Any],
    *,
    stateless: bool,
    headers: dict[str, str] | None = None,
    transport_security: TransportSecuritySettings | None = None,
    host: str = "127.0.0.1",
) -> AsyncIterator[httpx2.AsyncClient]:
    """Run the server's ASGI app (with its session manager) and yield an in-process client."""
    app = server.streamable_http_app(
        stateless_http=stateless,
        json_response=stateless,
        transport_security=transport_security,
        host=host,
    )
    async with (
        server.session_manager.run(),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE_URL, headers=headers
        ) as http,
    ):
        yield http


@asynccontextmanager
async def mcp_client(http: httpx2.AsyncClient) -> AsyncIterator[Client]:
    """Connect an MCP client to the app behind ``http``."""
    async with Client(streamable_http_client(MCP_URL, http_client=http)) as client:
        yield client


@pytest.fixture(params=["stateful", "stateless"])
def flavour(request: pytest.FixtureRequest) -> Flavour:
    """Run a test against both HTTP flavours."""
    return cast("Flavour", request.param)


@pytest.fixture
def fake() -> FakeStaan:
    """A fake Staan API answering one search result."""
    return FakeStaan(search=httpx2.Response(201, json=search_response("https://a.example")))


# --------------------------------------------------------------------------------------
# Plain HTTP serving
# --------------------------------------------------------------------------------------


async def test_tools_work_over_http(fake: FakeStaan, flavour: Flavour) -> None:
    async with (
        serve(make_server(fake), stateless=flavour == "stateless") as http,
        mcp_client(http) as client,
    ):
        names = sorted(tool.name for tool in (await client.list_tools()).tools)
        result = await client.call_tool("web_search", {"query": "python", "full_content": "none"})

    assert names == ["answer", "web_search"]
    assert result.is_error is False
    assert json.loads(text_of(result)) == [
        {
            "title": "Title https://a.example",
            "url": "https://a.example",
            "snippet": "Snippet https://a.example",
        }
    ]
    assert fake.body["q"] == "python"
    assert "full_content" not in fake.body


async def test_the_staan_client_opens_once_per_server_not_per_request(
    fake: FakeStaan, flavour: Flavour
) -> None:
    opened: list[object] = []
    inner = fake.client_factory()

    def factory(settings: Any) -> Any:
        client = inner(settings)
        opened.append(client)
        return client

    server = server_module.create_server(Settings(api_key=API_KEY), client_factory=factory)
    async with serve(server, stateless=flavour == "stateless") as http, mcp_client(http) as client:
        await client.call_tool("web_search", {"query": "a"})
        await client.call_tool("web_search", {"query": "b"})

    assert len(opened) == 1
    assert len(fake.requests) == 2


async def test_stateful_http_issues_a_session_stateless_http_does_not(
    fake: FakeStaan, flavour: Flavour
) -> None:
    async with serve(make_server(fake), stateless=flavour == "stateless") as http:
        response = await http.post("/mcp", headers=JSON_HEADERS, json=INITIALIZE)

    assert response.status_code == 200
    if flavour == "stateless":
        assert "mcp-session-id" not in response.headers
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["result"]["serverInfo"]["name"] == "staan"
    else:
        assert response.headers["mcp-session-id"]
        assert response.headers["content-type"].startswith("text/event-stream")


async def test_unknown_paths_are_not_found(fake: FakeStaan, flavour: Flavour) -> None:
    async with serve(make_server(fake), stateless=flavour == "stateless") as http:
        response = await http.get("/other")

    assert response.status_code == 404


async def test_a_post_without_a_json_content_type_is_rejected(
    fake: FakeStaan, flavour: Flavour
) -> None:
    async with serve(make_server(fake), stateless=flavour == "stateless") as http:
        response = await http.post(
            "/mcp", headers={**JSON_HEADERS, "Content-Type": "text/plain"}, content=b"x"
        )

    assert response.status_code == 400


# --------------------------------------------------------------------------------------
# DNS-rebinding protection
# --------------------------------------------------------------------------------------


async def test_loopback_hosts_are_accepted_by_default(fake: FakeStaan, flavour: Flavour) -> None:
    async with serve(make_server(fake), stateless=flavour == "stateless") as http:
        for host in ("127.0.0.1:8000", "localhost:8000", "[::1]:8000"):
            response = await http.post(
                "/mcp", headers={**JSON_HEADERS, "Host": host}, json=INITIALIZE
            )
            assert response.status_code == 200, host


@pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:8000", "127.0.0.2:8000"])
async def test_an_unexpected_host_header_gets_421(
    fake: FakeStaan, flavour: Flavour, host: str
) -> None:
    async with serve(make_server(fake), stateless=flavour == "stateless") as http:
        response = await http.post("/mcp", headers={**JSON_HEADERS, "Host": host}, json=INITIALIZE)

    assert response.status_code == 421
    assert fake.requests == []


async def test_an_unexpected_origin_gets_403(fake: FakeStaan, flavour: Flavour) -> None:
    async with serve(make_server(fake), stateless=flavour == "stateless") as http:
        bad = await http.post(
            "/mcp", headers={**JSON_HEADERS, "Origin": "https://evil.example.com"}, json=INITIALIZE
        )
        good = await http.post(
            "/mcp", headers={**JSON_HEADERS, "Origin": "http://localhost:3000"}, json=INITIALIZE
        )

    assert bad.status_code == 403
    assert good.status_code == 200


async def test_configured_allowed_hosts_replace_the_loopback_defaults(
    fake: FakeStaan, flavour: Flavour
) -> None:
    security = _security_settings(
        HttpConfig(allowed_hosts=("mcp.example.com", "mcp.example.com:*"))
    )

    async with serve(
        make_server(fake), stateless=flavour == "stateless", transport_security=security
    ) as http:
        statuses = {
            host: (
                await http.post("/mcp", headers={**JSON_HEADERS, "Host": host}, json=INITIALIZE)
            ).status_code
            for host in ("mcp.example.com", "mcp.example.com:8443", "127.0.0.1:8000", "evil.com")
        }

    assert statuses == {
        "mcp.example.com": 200,
        "mcp.example.com:8443": 200,
        "127.0.0.1:8000": 421,
        "evil.com": 421,
    }


async def test_configured_allowed_origins_are_enforced(fake: FakeStaan, flavour: Flavour) -> None:
    security = _security_settings(
        HttpConfig(allowed_hosts=("mcp.example.com",), allowed_origins=("https://app.example.com",))
    )

    async with serve(
        make_server(fake), stateless=flavour == "stateless", transport_security=security
    ) as http:
        host = {"Host": "mcp.example.com"}
        allowed = await http.post(
            "/mcp",
            headers={**JSON_HEADERS, **host, "Origin": "https://app.example.com"},
            json=INITIALIZE,
        )
        denied = await http.post(
            "/mcp",
            headers={**JSON_HEADERS, **host, "Origin": "https://other.example.com"},
            json=INITIALIZE,
        )

    assert allowed.status_code == 200
    assert denied.status_code == 403


# --------------------------------------------------------------------------------------
# OAuth resource-server gate
# --------------------------------------------------------------------------------------

TOKENS = {
    "good": AccessToken(
        token="good", client_id="client-1", scopes=["staan"], resource=RESOURCE_URL
    ),
    "no-scope": AccessToken(
        token="no-scope", client_id="client-1", scopes=[], resource=RESOURCE_URL
    ),
    "other-audience": AccessToken(
        token="other-audience",
        client_id="client-1",
        scopes=["staan"],
        resource="https://other.example.com/mcp",
    ),
    "expired": AccessToken(
        token="expired", client_id="client-1", scopes=["staan"], resource=RESOURCE_URL, expires_at=1
    ),
}


class FakeVerifier:
    """A token verifier that knows a fixed set of tokens."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the known access token, or ``None``."""
        self.seen.append(token)
        return TOKENS.get(token)


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch) -> FakeVerifier:
    """Make the server use a fake verifier instead of JWT verification."""
    fake_verifier = FakeVerifier()
    monkeypatch.setattr(server_module, "build_token_verifier", lambda _config: fake_verifier)
    return fake_verifier


@pytest.fixture
def auth_config() -> AuthConfig:
    """An OAuth gate that requires the ``staan`` scope."""
    return AuthConfig(
        issuer_url=ISSUER_URL,
        resource_url=RESOURCE_URL,
        required_scopes=("staan",),
        jwks_url=f"{ISSUER_URL}/jwks",
    )


@pytest.fixture
def gated(fake: FakeStaan, auth_config: AuthConfig, verifier: FakeVerifier) -> Callable[..., Any]:
    """Return ``serve`` pre-wired to a gated server."""
    assert verifier is not None
    return lambda **kwargs: serve(make_server(fake, auth=auth_config), **kwargs)


def bearer(token: str) -> dict[str, str]:
    """Build an Authorization header."""
    return {"Authorization": f"Bearer {token}"}


async def test_a_request_without_a_token_gets_401_with_resource_metadata(
    gated: Callable[..., Any], fake: FakeStaan, flavour: Flavour
) -> None:
    async with gated(stateless=flavour == "stateless") as http:
        response = await http.post("/mcp", headers=JSON_HEADERS, json=INITIALIZE)

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert 'error="invalid_token"' in challenge
    assert f'resource_metadata="{METADATA_URL}"' in challenge
    assert response.json()["error"] == "invalid_token"
    assert fake.requests == []


@pytest.mark.parametrize("token", ["unknown", "other-audience", "expired"])
async def test_an_invalid_token_gets_401(
    gated: Callable[..., Any], fake: FakeStaan, flavour: Flavour, token: str
) -> None:
    async with gated(stateless=flavour == "stateless") as http:
        response = await http.post(
            "/mcp", headers={**JSON_HEADERS, **bearer(token)}, json=INITIALIZE
        )

    assert response.status_code == 401
    assert f'resource_metadata="{METADATA_URL}"' in response.headers["www-authenticate"]
    assert fake.requests == []


async def test_a_token_without_the_required_scope_gets_403(
    gated: Callable[..., Any], fake: FakeStaan, flavour: Flavour
) -> None:
    async with gated(stateless=flavour == "stateless") as http:
        response = await http.post(
            "/mcp", headers={**JSON_HEADERS, **bearer("no-scope")}, json=INITIALIZE
        )

    assert response.status_code == 403
    challenge = response.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert "staan" in challenge
    assert fake.requests == []


async def test_a_valid_token_reaches_the_tools(
    gated: Callable[..., Any], verifier: FakeVerifier, flavour: Flavour
) -> None:
    async with (
        gated(stateless=flavour == "stateless", headers=bearer("good")) as http,
        mcp_client(http) as client,
    ):
        result = await client.call_tool("web_search", {"query": "python"})

    assert result.is_error is False
    assert "https://a.example" in text_of(result)
    assert "good" in verifier.seen


async def test_the_inbound_token_is_never_forwarded_to_staan(
    gated: Callable[..., Any], fake: FakeStaan, flavour: Flavour
) -> None:
    async with (
        gated(stateless=flavour == "stateless", headers=bearer("good")) as http,
        mcp_client(http) as client,
    ):
        await client.call_tool("web_search", {"query": "python"})

    assert fake.requests
    for request in fake.requests:
        assert request.headers["authorization"] == f"Bearer {API_KEY}"


async def test_protected_resource_metadata_describes_the_gate(
    gated: Callable[..., Any], flavour: Flavour
) -> None:
    async with gated(stateless=flavour == "stateless") as http:
        response = await http.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json() == {
        "resource": RESOURCE_URL,
        "authorization_servers": [f"{ISSUER_URL}/"],
        "scopes_supported": ["staan"],
        "bearer_methods_supported": ["header"],
    }


async def test_the_metadata_endpoint_needs_no_token(
    gated: Callable[..., Any], flavour: Flavour
) -> None:
    async with gated(stateless=flavour == "stateless") as http:
        response = await http.get("/.well-known/oauth-protected-resource/mcp")

    assert "www-authenticate" not in response.headers


async def test_dns_rebinding_protection_still_applies_with_the_gate(
    gated: Callable[..., Any], flavour: Flavour
) -> None:
    async with gated(stateless=flavour == "stateless") as http:
        response = await http.post(
            "/mcp",
            headers={**JSON_HEADERS, **bearer("good"), "Host": "evil.example.com"},
            json=INITIALIZE,
        )

    assert response.status_code == 421


async def test_without_the_gate_no_token_is_needed_and_no_metadata_is_served(
    fake: FakeStaan, flavour: Flavour
) -> None:
    async with serve(make_server(fake), stateless=flavour == "stateless") as http:
        initialize = await http.post("/mcp", headers=JSON_HEADERS, json=INITIALIZE)
        metadata = await http.get("/.well-known/oauth-protected-resource/mcp")

    assert initialize.status_code == 200
    assert metadata.status_code == 404


# --------------------------------------------------------------------------------------
# DNS-rebinding protection for every loopback spelling (the SDK special-cases only three)
# --------------------------------------------------------------------------------------

LOOPBACK_SPELLINGS = [
    "127.0.0.1",
    "localhost",
    "::1",
    "LOCALHOST",
    "[::1]",
    "127.0.0.2",
    "127.255.255.254",
    "0:0:0:0:0:0:0:1",
    " localhost",
]


@pytest.mark.parametrize("bind_host", LOOPBACK_SPELLINGS)
async def test_every_loopback_spelling_rejects_a_rebound_host_and_origin(
    fake: FakeStaan, flavour: Flavour, bind_host: str
) -> None:
    security = _security_settings(HttpConfig(host=bind_host))

    async with serve(
        make_server(fake),
        stateless=flavour == "stateless",
        transport_security=security,
        host=bind_host,
    ) as http:
        rebound = await http.post(
            "/mcp",
            headers={
                **JSON_HEADERS,
                "Host": "evil.example.com",
                "Origin": "http://evil.example.com",
            },
            json=INITIALIZE,
        )
        wrong_origin = await http.post(
            "/mcp",
            headers={**JSON_HEADERS, "Host": "localhost:8000", "Origin": "http://evil.example.com"},
            json=INITIALIZE,
        )
        genuine = await http.post(
            "/mcp",
            headers={**JSON_HEADERS, "Host": "127.0.0.1:8000", "Origin": "http://localhost:3000"},
            json=INITIALIZE,
        )

    assert rebound.status_code == 421
    assert wrong_origin.status_code == 403
    assert genuine.status_code == 200
    assert fake.requests == []


# --------------------------------------------------------------------------------------
# OAuth with real JWT verification: advertised URLs round-trip, sessions bind to the user
# --------------------------------------------------------------------------------------

ORIGIN_RESOURCE = "http://127.0.0.1:8000"
ORIGIN_ISSUER = "https://auth.example.com"


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    """The authorization server's signing key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StaticJWKClient:
    """A JWKS client that always returns the AS's public key."""

    def __init__(self, key: rsa.RSAPrivateKey) -> None:
        self._public = key.public_key()

    def get_signing_key_from_jwt(self, _token: str) -> SimpleNamespace:
        """Return the one key."""
        return SimpleNamespace(key=self._public)


def mint(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    """Sign an access token for the origin-only resource, with ``overrides`` applied."""
    claims: dict[str, Any] = {
        "iss": ORIGIN_ISSUER,
        "aud": ORIGIN_RESOURCE,
        "exp": int(time.time()) + 600,
        "sub": "alice",
        "azp": "shared-client",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture
def jwt_gated(
    fake: FakeStaan, signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Any]:
    """Return ``serve`` pre-wired to a server that verifies real JWTs for an origin-only URL."""
    config = AuthConfig(
        issuer_url=ORIGIN_ISSUER,
        resource_url=ORIGIN_RESOURCE,
        jwks_url=f"{ORIGIN_ISSUER}/jwks",
    )
    verifier = JWTTokenVerifier(config, jwk_client=StaticJWKClient(signing_key))
    monkeypatch.setattr(server_module, "build_token_verifier", lambda _config: verifier)
    return lambda **kwargs: serve(make_server(fake, auth=config), **kwargs)


async def test_the_advertised_resource_and_issuer_are_accepted_back_as_aud_and_iss(
    jwt_gated: Callable[..., Any], signing_key: rsa.RSAPrivateKey
) -> None:
    async with jwt_gated(stateless=True) as http:
        challenge = (await http.post("/mcp", headers=JSON_HEADERS, json=INITIALIZE)).headers[
            "www-authenticate"
        ]
        metadata_url = re.search(r'resource_metadata="([^"]+)"', challenge)
        assert metadata_url is not None
        metadata = (await http.get(urlsplit(metadata_url[1]).path)).json()

        # What a client that follows RFC 9728 and RFC 8707 would send.
        advertised = mint(
            signing_key,
            aud=metadata["resource"],
            iss=metadata["authorization_servers"][0],
        )
        configured = mint(signing_key)
        wrong_audience = mint(signing_key, aud="http://127.0.0.1:8000/other")
        statuses = {
            name: (
                await http.post("/mcp", headers={**JSON_HEADERS, **bearer(token)}, json=INITIALIZE)
            ).status_code
            for name, token in (
                ("advertised", advertised),
                ("configured", configured),
                ("wrong_audience", wrong_audience),
            )
        }

    # The SDK advertises origin-only URLs with a trailing slash; the configured spelling has none.
    assert metadata["resource"] == f"{ORIGIN_RESOURCE}/"
    assert metadata["authorization_servers"] == [f"{ORIGIN_ISSUER}/"]
    assert statuses == {"advertised": 200, "configured": 200, "wrong_audience": 401}


async def test_a_stateful_session_cannot_be_replayed_by_another_user_of_the_same_client(
    jwt_gated: Callable[..., Any], signing_key: rsa.RSAPrivateKey
) -> None:
    alice = mint(signing_key, sub="alice")
    bob = mint(signing_key, sub="bob")  # same client (azp), different resource owner
    list_tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}

    async with jwt_gated(stateless=False) as http:
        created = await http.post(
            "/mcp", headers={**JSON_HEADERS, **bearer(alice)}, json=INITIALIZE
        )
        session = {"mcp-session-id": created.headers["mcp-session-id"]}
        replayed_by_bob = await http.post(
            "/mcp", headers={**JSON_HEADERS, **bearer(bob), **session}, json=list_tools
        )
        used_by_alice = await http.post(
            "/mcp", headers={**JSON_HEADERS, **bearer(alice), **session}, json=list_tools
        )

    assert created.status_code == 200
    assert replayed_by_bob.status_code == 404
    assert used_by_alice.status_code == 200


# --------------------------------------------------------------------------------------
# The introspection verifier's HTTP client is closed with the server
# --------------------------------------------------------------------------------------


async def test_the_introspection_client_is_closed_when_the_server_stops(
    fake: FakeStaan, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AuthConfig(
        issuer_url=ISSUER_URL,
        resource_url=RESOURCE_URL,
        introspection_url=f"{ISSUER_URL}/introspect",
        client_id="mcp",
        client_secret="s3cret",
    )
    verifier = IntrospectionTokenVerifier(config)
    monkeypatch.setattr(server_module, "build_token_verifier", lambda _config: verifier)

    async with Client(make_server(fake, auth=config)) as client:
        await client.list_tools()
        assert verifier._http.is_closed is False

    assert verifier._http.is_closed is True


async def test_other_verifiers_are_left_alone_when_the_server_stops(
    fake: FakeStaan, auth_config: AuthConfig, verifier: FakeVerifier
) -> None:
    async with Client(make_server(fake, auth=auth_config)) as client:
        await client.list_tools()

    assert verifier.seen == []
