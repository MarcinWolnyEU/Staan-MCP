# MCP authorization for the Staan MCP server

Researched 2026-09-30. Experiments live in `.tmp/mcp-oauth/` (`exp_server.py`, `exp_as.py`, `exp_client.py`; they use mock tokens only, no real key).

## TL;DR recommendation

1. Staan has no OAuth for API access. The API takes one static key: `Authorization: Bearer <key>`. So there is nothing to delegate to and nothing to exchange.
2. The upstream credential is always the server-held `STAAN_API_KEY` env var. This is the default in every mode (stdio, http, stateless).
3. On HTTP, protect the endpoint. Bind to loopback by default, keep DNS-rebinding protection on, and refuse a non-loopback bind unless a gate is configured or the operator explicitly opts out.
4. Offer an optional OAuth 2.1 resource-server gate for HTTP. It is a pluggable `TokenVerifier` (introspection or JWT/JWKS) against an external authorization server that the operator runs. Tokens are validated (audience, expiry, scope) and never forwarded. The gate only decides who may use the key the server holds.
5. Per-request Staan key from the client is an opt-in escape hatch. Off by default. Use a dedicated header (`X-Staan-Api-Key`), never `Authorization`, and never combine it with OAuth mode. Do not log it.
6. Do not use Staan's own identity provider (`auth.staan.ai`) as the authorization server. See (a).

## (a) Does Staan offer OAuth or token exchange for API access? No.

Evidence:
- `docs.staan.ai` has 5 pages (`/sitemap.xml`): `/introduction`, `/docs/web-search`, `/docs/web-for-ai`, `/docs/answer-v2`, `/api`. `llms.txt`, `llms-full.txt` and `openapi.json` all return 404. The docs site is Zudoku.
- The docs state: "All requests require `Authorization: Bearer YOUR_API_KEY`". Base URL is `https://api.staan.ai/v2`. Sign up at staan.ai for a free key with 1,000 requests/month. There is no mention of OAuth, scopes, token endpoints or MCP.
- Endpoints seen in the docs: `POST /v2/answer` and `/v2/search/web`.
- The FAQ says: "From the console, you can create as many API keys as needed." This appears in the white-label / sub-licensing context. Console: `https://staan.ai/console`.
- `api.staan.ai/.well-known/oauth-authorization-server`, `.../oauth-protected-resource` and `.../openid-configuration` all return 404. The same three paths on `staan.ai` return 404 too.
- Upstream credential probes with fake credentials:
  - A JWT-shaped bearer (`eyJ...x.y`) gets `401 {"code":"INVALID_CREDENTIALS","message":"Unknown API key or invalid secret"}`. It is treated as an API key attempt, not as an OIDC token.
  - Bearer `abc.def` or `a.b.c`, or a missing header, or an `x-api-key` header, gets `401 {"message":"Unauthorized","statusCode":401}`.
  - Two distinct 401 body shapes exist, so the upstream error mapper must not assume one shape.
  - Real key format, structure only: opaque, 47 chars, `[A-Za-z0-9_]`, no dots.

There is an IdP, but it is for the console only:
- The website login uses OIDC against `https://auth.staan.ai`, which is ZITADEL (cookie `__Host-zitadel.useragent`). The SPA has `oidc-*.js` and `auth-*.js` chunks, `VITE_ZITADEL_DOMAIN` and `VITE_ZITADEL_CLIENT_ID`, redirect `https://staan.ai/auth/callback`, and console roles `awaiting-approval`, `customer`, `developer`, `admin`.
- Its discovery document is at `https://auth.staan.ai/.well-known/openid-configuration`. The RFC 8414 path `/.well-known/oauth-authorization-server` returns 404.
- Metadata: issuer `https://auth.staan.ai`, endpoints under `/oauth/v2/` (`authorize`, `token`, `introspect`, `revoke`, `keys`, `device_authorization`), grants including `authorization_code`, `refresh_token`, `client_credentials`, `jwt-bearer` and `device_code`, PKCE `S256`, scopes `openid profile email phone address offline_access`.
- It has no `registration_endpoint` (no DCR) and no `client_id_metadata_document_supported` (no CIMD).
- Nothing suggests `api.staan.ai` accepts these tokens. The JWT probe above says it does not. This is an inference, since I have no valid console token to test.
- It is not a viable authorization server for our MCP server. Its tokens are not issued for our resource (audience), and MCP clients cannot register against it. Accepting them would break the audience-binding MUST.

## (b) What the MCP authorization spec requires of a resource server

Versions:
- The current spec is **2026-07-28** (the versioning page says "current protocol version is 2026-07-28"). Earlier: 2025-11-25, 2025-06-18, 2025-03-26. A draft is in progress.
- 2025-06-18 introduced the RS/AS split, RFC 9728 and RFC 8707. 2025-11-25 added Client ID Metadata Documents (CIMD) and OIDC discovery alongside RFC 8414. 2026-07-28 deprecates DCR in favour of CIMD and adds RFC 9207 `iss` handling for clients.
- 2026-07-28 also removes protocol-level sessions and `Mcp-Session-Id`, and removes the `initialize` handshake. MCP is stateless: every request carries its protocol version and client capabilities in `_meta`. It adds the mandatory `server/discover` RPC, and `Mcp-Method` / `Mcp-Name` headers on Streamable HTTP POST. Its changelog lists no other authorization changes.

Normative points for a server acting as RS (all versions from 2025-06-18 unless noted):
- Authorization is OPTIONAL. HTTP transports SHOULD conform. **stdio SHOULD NOT use it and should take credentials from the environment.** That matches `STAAN_API_KEY`.
- The server acts as an OAuth 2.1 resource server. **MUST implement RFC 9728 Protected Resource Metadata.** The document MUST contain `authorization_servers` (at least one).
- The server MUST advertise it by at least one of:
  - `WWW-Authenticate: Bearer resource_metadata="<url>"` on 401, or
  - a well-known URI, either `/.well-known/oauth-protected-resource<path>` or the root form.
- The 401 `WWW-Authenticate` SHOULD carry `scope="..."`. `scopes_supported` in the PRM is the fallback.
- MUST validate access tokens per OAuth 2.1 §5.2.
- **MUST validate the token was issued for this server as audience** (RFC 8707, `aud` / resource indicator).
- Invalid or expired token: HTTP 401. Insufficient scope: 403 with `error="insufficient_scope"`, `scope="..."` and `resource_metadata`. Malformed request: 400.
- The client must send `Authorization: Bearer` on **every** HTTP request. Tokens MUST NOT go in the query string.
- **"MCP servers MUST NOT accept or transit any other tokens."** This is the token-passthrough ban. Text from the 2025-11-25 authorization spec, kept in later versions: "If the MCP server makes requests to upstream APIs, it may act as an OAuth client to them. The access token used at the upstream API is a separate token, issued by the upstream authorization server. The MCP server MUST NOT pass through the token it received from the MCP client."
- Servers SHOULD NOT put `offline_access` in the challenge scope or in `scopes_supported`.
- The AS side (not ours): PKCE `S256`, CIMD SHOULD, DCR MAY (deprecated), exact redirect URI match, and `iss` in the authorization response SHOULD.
- The security best practices page adds:
  - Token passthrough is an anti-pattern ("MUST NOT accept any tokens that were not explicitly issued for the MCP server").
  - The confused-deputy rules for MCP proxy servers that use a static client ID against a third-party AS. Not relevant to us, because we are not an OAuth proxy.
  - Loopback servers should use stdio, or require an auth token, when they use HTTP.

## (c) python-sdk (`mcp`) support, verified in scratch

Versions on PyPI (all `requires_python >=3.10`):
- **2.2.0** (2026-09-07) is the latest.
- 2.0.0 was released 2026-07-28.
- 1.30.0 (2026-09-07) is the last 1.x line.

Verified against installed **mcp 2.2.0** (a venv with `httpx` 0.28.1, `httpx2`, `starlette` 1.7.0, `uvicorn` 0.54.0, `pydantic` 2.13.5) and 1.30.0.

v1 to v2 breaking changes that matter to the project:
- `FastMCP` is renamed to `MCPServer`, imported as `from mcp.server.mcpserver import MCPServer`.
- `mcp.server.fastmcp` now raises `ModuleNotFoundError` with a migration hint.
- Transport arguments moved from the constructor to `run()` and `streamable_http_app()`: `host`, `port`, `streamable_http_path`, `json_response`, `stateless_http`, `transport_security` and others.
- `MCPServer.get_context()` is removed. Tools take a `ctx: Context` parameter, and headers are read from `ctx.headers`, which is `None` on stdio.
- The SDK's client uses `httpx2` (an httpx fork). The `mcp.Client(url_or_transport)` high-level client is handy for tests.
- Pin `mcp>=2.2,<3` for spec 2026-07-28 support, or stay on `mcp<2` (1.30). The 1.30 auth API is the same shape: `FastMCP`, `TokenVerifier`, and `AuthSettings` without `identity_assertion_enabled`.

Auth classes (same names in 1.30 and 2.2):

| Class / function | Module | Notes |
|---|---|---|
| `TokenVerifier` (Protocol) | `mcp.server.auth.provider` | `async verify_token(token) -> AccessToken \| None` |
| `AccessToken` | same | `token, client_id, scopes, expires_at, resource, subject, claims` (2.x adds `subject` and `claims`) |
| `AuthSettings` | `mcp.server.auth.settings` | `issuer_url`, `resource_server_url`, `required_scopes`, `validate_token_resource`, plus AS-only fields (`client_registration_options`, `revocation_options`, `service_documentation_url`, 2.x `identity_assertion_enabled`) |
| `OAuthAuthorizationServerProvider` | `mcp.server.auth.provider` | For running your own AS. Not needed for an RS-only design. |
| `BearerAuthBackend` | `mcp.server.auth.middleware.bearer_auth` | Starlette auth backend that calls the verifier. |
| `RequireAuthMiddleware` | same | Returns 401 or 403 and adds the `WWW-Authenticate` header. |
| `get_access_token()` | `mcp.server.auth.middleware.auth_context` | Returns the authenticated `AccessToken` inside a tool. |
| `create_protected_resource_routes`, `build_resource_metadata_url` | `mcp.server.auth.routes` | RFC 9728 route. |

Wiring:
- `MCPServer("name", token_verifier=V(), auth=AuthSettings(issuer_url=..., resource_server_url=..., required_scopes=[...], validate_token_resource=True))`.
- Passing `auth` without `token_verifier` or `auth_server_provider` raises `ValueError`. Passing both providers raises `ValueError`. So does passing a verifier without `auth`.
- With `resource_server_url` set, `validate_token_resource` **must be set explicitly** or an `MCPDeprecationWarning` fires. It will default to True in 3.0. Set True when the AS binds the `resource`; set False only if your verifier checks `aud` itself. If True, the verifier must set `AccessToken.resource` to the resource URL (the matching `aud` entry).

Experiment results:
- No token: `401`, `WWW-Authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="http://127.0.0.1:8765/.well-known/oauth-protected-resource/mcp"`.
- PRM at `/.well-known/oauth-protected-resource/mcp` returned `{"resource":..., "authorization_servers":[...], "scopes_supported":["staan:search"], "bearer_methods_supported":["header"]}` with `cache-control: public, max-age=3600`.
- Introspection verifier (mock RFC 7662 server) and JWT verifier (PyJWT[crypto], RS256, `audience=`, `issuer=`, required claims):

| Token | HTTP status |
|---|---|
| Good | 200 |
| Wrong audience | 401 |
| Expired | 401 |
| Wrong issuer | 401 |
| Missing required scope | 403 with `error="insufficient_scope"` |

- Stateless mode (`run("streamable-http", stateless_http=True)`) works together with auth and the tool saw `get_access_token()`.
- Stateful mode answers a raw POST without a session ID with `400 "Missing session ID"`, so tests should use `mcp.Client` or stateless mode.
- Loopback hosts get DNS-rebinding protection automatically. A request with `Host: evil.example` returned `421`. The SDK only does this when the bind host is loopback and `transport_security` is None. For any other bind we must pass `TransportSecuritySettings(allowed_hosts=..., allowed_origins=...)` ourselves.
- `ctx.headers` exposes request headers (including `Authorization` and `X-Staan-Api-Key`) inside a tool. The SDK did not log the secret values.

SDK gaps to work around or accept:
- The no-token 401 includes `error="invalid_token"`. RFC 6750 §3.1 says a request with no credentials SHOULD NOT carry an error code. A small subclass of `RequireAuthMiddleware` could fix it.
- The 401 challenge has no `scope=` parameter. The spec says SHOULD. `scopes_supported` in the PRM covers the fallback.
- The auth wiring in the SDK is marked `# pragma: no cover` upstream, so our own 100 % coverage needs our own tests around our verifier classes and app factory (in-process ASGI plus a mock introspection/JWKS endpoint).

## (d) Options assessed

| Option | Security | Fit | Verdict |
|---|---|---|---|
| A. `STAAN_API_KEY` env only | Secret stays server-side and never reaches the model or the client. On stdio the transport is a private pipe. On HTTP with no gate, anyone who can reach the port spends your key. | Matches spec guidance for stdio (credentials from env). | Default for stdio. For HTTP, needs a gate or a loopback-only bind. |
| B. Per-request key via `Authorization: Bearer <staan key>` | Forwarding a client-supplied bearer upstream is structurally token passthrough. It conflicts with "MUST NOT accept or transit any other tokens", and with clients that read a 401 as the start of an OAuth flow. Loggers and proxies treat `Authorization` as an OAuth credential. | Simple for users. | **Reject as a default.** |
| C. Per-request key via `X-Staan-Api-Key` (a custom header) | This is bring-your-own-key. The key is not an OAuth token from the MCP AS, so it sits outside the MCP authorization flow. The server does not validate it, so any caller can burn whatever key they supply. That is their key, so the exposure is theirs. It needs TLS, no logging and no session caching. | Useful for hosted or shared deployments where each user has a Staan key. | **Opt-in only** (`STAAN_MCP_ALLOW_CLIENT_KEY=1`). Disabled in stdio. Mutually exclusive with OAuth mode. Precedence: header key over env key, and 401 if none. |
| D. OAuth RS gate over the server-held key | Standards-compliant: RFC 9728 PRM, audience-bound tokens, scopes, 401/403 semantics. The inbound token and the upstream key are different credentials and never mix. Cost: the operator must run an AS (Keycloak, ZITADEL, Auth0, Entra, and so on). | Right for multi-user hosted deployments. | **Optional feature for HTTP and stateless modes.** |
| E. Own the AS (`OAuthAuthorizationServerProvider`) | Highest risk and effort. We would take on consent UIs and confused-deputy duties. | Overkill for a key-based upstream. | **Reject.** |
| F. Use `auth.staan.ai` as the AS | Not our audience, no DCR or CIMD, meant for console login. | n/a | **Reject.** |
| G. Static shared bearer secret gate (`STAAN_MCP_AUTH_TOKEN`) | Constant-time comparison, but it is not OAuth: no PRM, no AS discovery, so MCP clients cannot auto-authorize. Manual header config only. | Cheap self-hosting protection. | Optional. Label it clearly as "not spec OAuth". |

## Concrete design for the implementer

- Config (env, with CLI overrides):
  - `STAAN_API_KEY` (required unless client-key mode is on).
  - `STAAN_MCP_AUTH_ISSUER_URL`, `STAAN_MCP_RESOURCE_URL` (canonical, e.g. `https://mcp.example.com/mcp`), `STAAN_MCP_AUTH_SCOPES` (default `staan:search staan:answer`).
  - One of `STAAN_MCP_AUTH_JWKS_URL` (JWT + JWKS) or `STAAN_MCP_AUTH_INTROSPECTION_URL` plus `..._CLIENT_ID` / `..._CLIENT_SECRET`.
  - `STAAN_MCP_ALLOW_CLIENT_KEY`, `STAAN_MCP_ALLOWED_HOSTS`, `STAAN_MCP_ALLOWED_ORIGINS`.
- Startup rules:
  - HTTP on a non-loopback host requires an auth gate or `--insecure-no-auth`, and requires `allowed_hosts`.
  - OAuth mode and client-key mode together are rejected.
  - Issuer and resource URLs must be `https` (except loopback).
- Verifier contract:
  - Introspection: POST the token with client auth, require `active`, `aud` containing the resource URL, `exp` in the future, and the scopes. Set `AccessToken.resource` to the resource URL only when `aud` matches.
  - JWT: `algorithms` allowlist (never `none`), `audience`, `issuer`, `require=["exp","aud","iss"]`, JWKS caching with a `kid` refresh.
  - Return `None` on any failure. Never raise.
- Upstream call: always `Authorization: Bearer <server key or client key>`, built fresh per request. Never forward the inbound token.
- Redact `Authorization`, `X-Staan-Api-Key` and the key value in logs, errors and tool results.
- Error mapping: treat upstream 401 as a config or credential problem. Do not expose the upstream body verbatim. It may echo details, and there are two body shapes (see (a)).
- Tests:
  - Unit tests with mocked upstream and verifier.
  - In-process ASGI tests for: no token, bad audience, expired, wrong issuer, insufficient scope, PRM contents, loopback Host rejection (421), stateless mode, and the client-key precedence rules.
  - Verified by experiment: the failure statuses in the table above.
- Docs to write for users: a threat-model page covering stdio (env), HTTP without a gate (loopback only), OAuth gate (with an example against a generic OIDC provider), and client-key mode, with a clear warning that it is not OAuth.

## Sources

- https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- https://modelcontextprotocol.io/specification/2026-07-28/changelog
- https://modelcontextprotocol.io/specification/versioning
- https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- https://modelcontextprotocol.io/specification/draft/basic/security_best_practices
- https://py.sdk.modelcontextprotocol.io/v2/migration/
- https://docs.staan.ai/introduction, https://docs.staan.ai/sitemap.xml, https://staan.ai/faq
- https://auth.staan.ai/.well-known/openid-configuration
- Installed `mcp` 2.2.0 and 1.30.0 source (`mcp/server/auth/*`, `mcp/server/mcpserver/server.py`)
