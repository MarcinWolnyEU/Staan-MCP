# MCP Python SDK research (verified 2026-09-30)

All snippets below were executed against `mcp==2.2.0` in `.tmp/mcp-python-sdk/` (venv, scripts in `.tmp/mcp-python-sdk/verify/`).

## 1. Versions and the big headline: v2 renamed FastMCP

- PyPI `mcp` latest stable: **2.2.0** (2026-09-07). Requires Python `>=3.10` (classifiers 3.10-3.14). GitHub latest release tag `v2.2.0`. Docs: https://py.sdk.modelcontextprotocol.io/ (sources in repo `docs/`, tag `v2.2.0`).
- v1 line still maintained: latest **1.30.0** (2026-09-07), branch `v1.x`. Official reference servers (e.g. `mcp-server-fetch` 2026.8.18) still pin `mcp>=1.29.0,<2`, because "SDK 2.0 renamed APIs".
- **`mcp.server.fastmcp` no longer exists in 2.x.** Importing it raises `ModuleNotFoundError` with a migration hint. Use:
  - `from mcp.server import MCPServer` (same class as `mcp.server.mcpserver.MCPServer`)
  - `from mcp.server.mcpserver import Context`
  - `from mcp.server.mcpserver.exceptions import ToolError`
  - `from mcp.types import ToolAnnotations` (`mcp.types` re-exports from the new standalone `mcp-types` package, pinned `==2.2.0`)
  - Client: `from mcp import Client` (new unified client; also `StdioServerParameters` from `mcp.client.stdio`)
- Recommendation for Staan-MCP: depend on `mcp>=2.2,<3`, use `MCPServer`. Do not support v1.
- 2.x dependency set (relevant for packaging): anyio, `httpx2>=2.5.0` (SDK switched off `httpx`; validates via OS trust store), jsonschema, mcp-types, opentelemetry-api, pydantic>=2.12, pyjwt[crypto], python-multipart, pywin32 (Windows only), sse-starlette, starlette, typing-extensions, typing-inspection, uvicorn. Extras: `cli` (typer, python-dotenv), `rich`. `mcp dev` / `mcp run` need `mcp[cli]`.
- Key v2 breaking changes (from docs/whats-new.md): all Python attrs snake_case (`result.is_error`, `tool.input_schema`, `structured_content`, `next_cursor`); transport config (`host`, `port`, ...) moved from constructor to `run()`; sync tool functions run on worker threads; Streamable HTTP lifespan runs ONCE at startup (not per session); WebSocket transport, Tasks API, `McpError` (now `MCPError`), `MCPServer.get_context()`, `mount_path` removed; URI templates strictly RFC 6570.

## 2. MCP spec version

- Current spec: **2026-07-28** (modelcontextprotocol.io/specification/versioning). Previous: 2025-11-25 (and earlier 2025-06-18, 2025-03-26, 2024-11-05).
- `mcp.types.LATEST_PROTOCOL_VERSION == "2026-07-28"`. SDK 2.2 serves BOTH 2025-11-25 (legacy, `initialize` handshake, sessions) and 2026-07-28 (modern: no handshake, each request carries `io.modelcontextprotocol/protocolVersion` in `_meta`, `server/discover`, no server-initiated requests, `ping` removed) simultaneously, no config.
- Logging/roots/sampling are deprecated as of 2026-07-28: `ctx.info()/debug()/warning()/error()/log()` emit `MCPDeprecationWarning` (verified). For pytest use `filterwarnings` carefully (or log with stdlib `logging` to stderr instead of `ctx.info`). Recommendation: use the stdlib `logging` module, not `ctx.*` logging.
- Modern HTTP requests need headers `MCP-Protocol-Version: 2026-07-28`, `Mcp-Method: <method>` (and `Mcp-Name` for tool/prompt/resource calls). A raw POST with the version header but no `Mcp-Method` gets `400` code `-32020` "mcp-method header does not match the request body's method". Verified working raw modern call: response `{"result":{"content":[...],"isError":false,"resultType":"complete","_meta":{"io.modelcontextprotocol/serverInfo":{...}}}}`.

## 3. Defining tools (verified)

```python
from typing import Annotated, Literal, NotRequired, TypedDict
from pydantic import Field
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

mcp = MCPServer("staan", version="0.1.0", instructions="...", lifespan=lifespan)

class Hit(TypedDict):
    title: str
    url: str
    full_content: NotRequired[str]

@mcp.tool(
    title="Search",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True,
                                idempotent_hint=True, destructive_hint=False),
)
async def search(
    query: Annotated[str, Field(description="Query", min_length=1)],
    market: Annotated[str | list[str], Field(description="Market(s), globs allowed")] = "en-gb",
    count: Annotated[int, Field(ge=1, le=50, description="Result count")] = 10,
    full_content: Literal["markdown", "text", "none"] = "markdown",
    simple_response: bool = True,
    ctx: Context[AppCtx] = None,   # injected, never in the schema
) -> list[Hit]:
    """Docstring becomes the tool description."""
```

Verified behaviour:
- `@mcp.tool()` MUST be called with parentheses (`@mcp.tool` raises TypeError). Options: `name, title, description, annotations, icons, meta, structured_output`. `mcp.add_tool(fn, ...)` is the imperative form.
- Decorator returns the original function, so it is directly unit-testable.
- Input schema (JSON Schema 2020-12): `Field(ge/le)` -> `minimum/maximum`; `Literal` -> `enum`; `str | list[str]` -> `anyOf:[{type:string},{type:array,items:{type:string}}]`; defaults -> `default` and not in `required`; `Field(description=)` -> `description`. Context param is excluded.
- Constraint violation is rejected before the function runs, returned as `is_error=True` with text like `Error executing tool search: 1 validation error for searchArguments\ncount\n  Input should be less than or equal to 50 [type=less_than_equal, ...]`.
- `from __future__ import annotations` works together with `Context` and `Annotated`.
- Sync `def` tools run in a worker thread; `async def` is awaited.
- Annotation field names (Python snake_case; wire camelCase): `read_only_hint`->`readOnlyHint`, `open_world_hint`->`openWorldHint`, `destructive_hint`, `idempotent_hint`, `title`. Serialised annotations verified: `{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}`.

## 4. Structured output / output schemas (verified)

- Return annotation IS the output schema (`structured_output=None` auto-detects; `True`/`False` force).
- Scalars, lists get wrapped: `list[Hit]` -> outputSchema `{"properties":{"result":{"type":"array","items":{"$ref":"#/$defs/Hit"}}},"required":["result"]}`; `structured_content == {"result":[...]}`. Text `content` for a list is one `TextContent` per item (JSON text of each).
- `dict[str, str]` -> `{"additionalProperties":{"type":"string"},"type":"object"}`, `structured_content == {"answer": "..."}` (NOT wrapped), `content` has pretty-printed JSON text.
- `BaseModel`, `TypedDict`, dataclass -> object schema directly, no wrapper. `NotRequired` keys absent from the dict stay out of `structured_content`.
- `structured_output=False` -> no output schema, `structured_content is None`, only text.
- Important consequence for the Staan `simple_response` design: a top-level array return (`list[Hit]`) is always wrapped as `{"result": [...]}` in `structured_content`. If the exact top-level JSON array must be seen by clients, return a JSON string with `structured_output=False`, or return `mcp.types.CallToolResult` directly (see `examples/mcpserver/direct_call_tool_result_return.py`), otherwise accept the `result` wrapper (content text is still the items). Also returned data is validated against the output schema, so a union return type like `list[Hit] | dict[str, str]` (simple vs full response) would need `structured_output=False` or a loose `Any`/`dict` return.

## 5. Context, lifespan, errors

- `ctx: Context[AppCtx]` (tool-only spelling; resources/prompts use bare `Context`). Members verified present: `request_context`, `report_progress`, `elicit`, `read_resource`, `info/debug/warning/error/log` (deprecated), `mcp_server`, `headers`, `protocol_version`, `request_id`, `session`, `notify_*`. `ctx.request_context.lifespan_context` is what the lifespan yielded (empty `{}` when none).
- Lifespan (verified): `@asynccontextmanager async def lifespan(server: MCPServer): ... yield AppCtx(...)`; `MCPServer("x", lifespan=lifespan)`. Runs once around server life (including in HTTP mode in 2.x). Good place for a shared `httpx.AsyncClient` (call `aclose()` in `finally`).
- Errors (verified with in-memory client):
  - `raise ToolError("msg")` -> result `is_error=True`, content `Error executing tool search: msg`. The model sees the message. Use for upstream API errors and bad input.
  - any other exception -> `is_error=True`, content only `Error executing tool search` (details go to server log at ERROR). Never leaks internals.
  - `raise mcp.MCPError(code, message, data)` -> whole request fails with a JSON-RPC error (no result). Use only when a smarter model could not fix it.
  - Never `return` an error string (it would have `is_error=False`).
  - Bad args (schema validation) -> `is_error=True` with pydantic message, before your function runs.

## 6. Transports and running (verified)

```python
mcp.run()                                   # stdio (default), synchronous, blocks
mcp.run("streamable-http", host="127.0.0.1", port=8000,
        streamable_http_path="/mcp", stateless_http=True, json_response=True)
```
- `run(transport)` accepts `"stdio" | "sse" | "streamable-http"`. Options are keyword args of `run()` (NOT the constructor; `MCPServer(port=...)` -> TypeError).
- streamable-http kwargs: `host` (default `127.0.0.1`), `port` (8000), `streamable_http_path` (`/mcp`), `json_response` (False), `stateless_http` (False), `event_store`, `retry_interval`, `max_request_body_size` (4 MiB, 413 above), `session_idle_timeout` (1800 s), `max_sessions` (10000), `transport_security`.
- SSE: `run("sse", sse_path="/sse", message_path="/messages/")` still works but is superseded (since 2025-03-26 spec), "Don't build anything new on it". Do not offer it.
- `json_response=True`: each POST answered with one JSON body instead of SSE. Progress/log notifications and back-channel (elicitation/sampling) not available on that leg. Fine for a plain search server.
- `stateless_http=True`: legacy (2025-11-25) clients get no session, no `Mcp-Session-Id`, any worker can serve any request. On 2026-07-28 the modern path is sessionless by construction, so the flag only matters for legacy. Verified: legacy `initialize` returned 200 JSON with no session header; tools/call without any `initialize` also succeeds under stateless mode; SDK `Client(url)` connected and called tools against it.
- stdio: stdout is the wire. The SDK diverts flushed stdout writes to stderr while serving; use `logging` (stderr). `mcp.run()` for stdio verified end-to-end with `Client(StdioServerParameters(command=sys.executable, args=[...], env={...}))`.
- **DNS-rebinding protection is on by default for HTTP**: only `Host` of `127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>` accepted; anything else gets `421 Misdirected Request` ("Invalid Host header"), bad Origin gets 403. Behind a real hostname pass
  `transport_security=TransportSecuritySettings(allowed_hosts=["mcp.example.com","mcp.example.com:*"], allowed_origins=[...])` (also fields `enable_dns_rebinding_protection`). Verified 421 for `Host: evil.example.com`. Import: `from mcp.server.transport_security import TransportSecuritySettings`. Note `host=` passed to `run()` also feeds the default allowlist logic; when binding `0.0.0.0` provide an explicit allowlist (design decision for the CLI: `--allowed-host` flags).
- Behind TLS proxy: `uvicorn ... --proxy-headers --forwarded-allow-ips=...` (only relevant if serving ASGI app via uvicorn directly).
- No `workers=` option; multi-process = hand `streamable_http_app()` to uvicorn/gunicorn.

## 7. ASGI / Starlette mounting (verified)

```python
import contextlib
from starlette.applications import Starlette
from starlette.routing import Mount

_mcp_app = mcp.streamable_http_app(stateless_http=True, json_response=True,
                                   transport_security=TransportSecuritySettings(...))

@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():   # mandatory when mounted
        yield

app = Starlette(routes=[Mount("/", app=_mcp_app)], lifespan=lifespan)   # uvicorn mount:app
```
- `mcp.streamable_http_app(**same kwargs as run(), minus port)` returns a Starlette app (route `/mcp`, built-in lifespan starting `mcp.session_manager`). `mcp.session_manager` exists only after `streamable_http_app()` was called. A mounted sub-app's lifespan never runs, so the host lifespan MUST enter `mcp.session_manager.run()` (else `RuntimeError: Task group is not initialized. Make sure to use run().`).
- Own routes must come before `Mount("/")`. `streamable_http_path="/"` makes the mount prefix the whole endpoint.
- `@mcp.custom_route("/health", methods=["GET"])` adds a plain Starlette handler, never authenticated (good for health checks; keep it private-data-free).
- CORS for browsers: `allow_headers` incl. `Mcp-*`/Content-Type, `expose_headers=["Mcp-Session-Id"]`, methods GET/POST/DELETE, and mirror `allowed_origins` in transport security.

## 8. Testing (verified with pytest + anyio)

```python
import pytest
from mcp import Client
from server import mcp

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c

@pytest.mark.anyio
async def test_x(client):
    r = await client.call_tool("search", {"query": "x"})
    assert r.is_error is False
    r.structured_content   # dict; r.content -> list[TextContent]; r.meta contains server identity stamp
    tools = (await client.list_tools()).tools   # tool.input_schema / .output_schema / .annotations.read_only_hint
```
- v1's `create_connected_server_and_client_session` is gone from the documented path; `Client(server_obj)` is the in-memory equivalent (no subprocess, no port). Takes a `MCPServer`, low-level `Server`, transport, `StdioServerParameters`, or URL string. Constructor kwargs: `raise_exceptions`, `read_timeout_seconds`, `mode` (`"auto"` default era-neutral, `"legacy"` to test legacy semantics), `client_info`, callbacks.
- `raise_exceptions=True` only exposes failures OUTSIDE the tool body; a tool exception is always `is_error=True` regardless. Assert on the result; crash tracebacks are in server log (use `caplog`).
- pytest-asyncio also works (I ran with anyio plugin; both installed). SDK docs use `@pytest.mark.anyio` + `anyio_backend` fixture and `inline-snapshot`.
- HTTP layer without a real port (verified):
  ```python
  app = mcp.streamable_http_app(stateless_http=True, json_response=True)
  async with mcp.session_manager.run():
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c:
          r = await c.post("/mcp", headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"},
                           json={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"plain","arguments":{"x":5}}})
  ```
  returns 200 `{"jsonrpc":"2.0","id":1,"result":{"content":[...],"isError":false}}`; `Host: evil.com` -> 421. In v1 `session_manager.run()` could only be entered once per app instance (NOT re-verified in 2.2), so build a fresh server/app per test to be safe.
- Real subprocess/port e2e also verified: `Client("http://127.0.0.1:8765/mcp")` and stdio `Client(StdioServerParameters(...))` both work.
- Note: the in-memory Client skips the HTTP layer, so auth and transport security are NOT exercised by it.

## 9. Authorization / OAuth (server side)

- Over HTTP the server is an OAuth 2.1 **resource server** only: it verifies bearer tokens and advertises the authorization server; it never issues tokens (and the SDK ships no login UI).
- API (verified importable and constructing):
  ```python
  from mcp.server.auth.provider import AccessToken, TokenVerifier
  from mcp.server.auth.settings import AuthSettings
  from mcp.server.auth.middleware.auth_context import get_access_token   # None on stdio / in-memory
  class V(TokenVerifier):
      async def verify_token(self, token: str) -> AccessToken | None: ...
  mcp = MCPServer("x", token_verifier=V(), auth=AuthSettings(
      issuer_url="https://auth.example.com", resource_server_url="https://host/mcp",
      required_scopes=[...], validate_token_resource=False))
  ```
  `AccessToken(*, token, client_id, scopes, expires_at=None, resource=None, subject=None, claims=None)`. `token_verifier=` and `auth=` must be passed together (ValueError otherwise). Unset `validate_token_resource` with `resource_server_url` emits `MCPDeprecationWarning` (3.0 default becomes True), so always set it explicitly.
  The app then exposes `/mcp` plus `/.well-known/oauth-protected-resource/mcp` (RFC 9728), and unauthenticated requests get `401` with `WWW-Authenticate: Bearer error="invalid_token", ... resource_metadata="..."`.
- stdio has no Authorization header: auth is never consulted (trust boundary = launching process). Use env `STAAN_API_KEY` there.
- `auth_server_provider=` (embedded authorization server) exists but the docs say new servers should not use it.
- Implication for Staan: Staan uses a plain API key (no OAuth AS is known). The MCP server cannot make a caller "log in with Staan", so: stdio = `STAAN_API_KEY` env; HTTP = either server-side `STAAN_API_KEY` env shared by all callers (protect the endpoint with an optional static bearer `TokenVerifier` from env, e.g. `STAAN_MCP_AUTH_TOKEN`, compared with `hmac.compare_digest`), or (multi-tenant) let the client pass its own Staan key as the bearer token and use it upstream via `get_access_token().token`. A verifier that accepts any non-empty token and forwards it upstream is a viable pass-through design; note this requires `AuthSettings` (issuer_url is a mandatory field even for static-token setups, so pick the deployment URL/placeholder and document it).

## 10. Packaging patterns of high-quality Python MCP servers

- Reference: `mcp-server-fetch` (modelcontextprotocol/servers/src/fetch): hatchling build backend, `[project.scripts] mcp-server-fetch = "mcp_server_fetch:main"`, `__main__.py` calling `main()`, src layout, `requires-python >=3.10`, README shows `"command": "uvx", "args": ["mcp-server-fetch"]` and `pip install` + `python -m mcp_server_fetch`. Dev tooling: pyright, ruff, pytest, pytest-asyncio; `[tool.pytest.ini_options] asyncio_mode = "auto"`.
- README must contain the registry ownership line, verbatim style used there: `<!-- mcp-name: io.github.modelcontextprotocol/server-fetch -->`.
- Client config for users (uvx): `{"mcpServers": {"staan": {"command": "uvx", "args": ["staan-mcp"], "env": {"STAAN_API_KEY": "..."}}}}`. `uvx staan-mcp` requires the console script name to equal the package name, or use `uvx --from staan-mcp <script>`.
- Also expose `python -m staan_mcp` via `__main__.py`.

## 11. Official MCP Registry publication

- Registry: `registry.modelcontextprotocol.io`, publisher CLI `mcp-publisher` (registry repo latest release **v1.8.1**). Commands: `mcp-publisher init | login github | login github-oidc | publish | validate | logout`. Registry is in preview.
- `server.json` (schema `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`, HTTP 200 verified, newest in repo `internal/validators/schemas/`):
  ```json
  {
    "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
    "name": "io.github.MarcinWolnyEU/staan-mcp",
    "title": "Staan",
    "description": "MCP server for the Staan search and answer API.",
    "repository": {"url": "https://github.com/MarcinWolnyEU/Staan-MCP", "source": "github"},
    "version": "0.1.0",
    "packages": [{
      "registryType": "pypi",
      "registryBaseUrl": "https://pypi.org",
      "identifier": "staan-mcp",
      "version": "0.1.0",
      "runtimeHint": "uvx",
      "transport": {"type": "stdio"},
      "environmentVariables": [
        {"name": "STAAN_API_KEY", "description": "Staan API key", "isRequired": true, "isSecret": true}
      ]
    }]
  }
  ```
  (PyPI example structure from registry docs `generic-server-json.md` and `package-types.mdx`; `name` must start with `io.github.<github-username>/` for GitHub auth. Case sensitivity of the username segment is not documented: use exactly the GitHub login casing `MarcinWolnyEU` and run `mcp-publisher validate`.) `identifier` must be the exact PyPI project name and `version` a version that exists on PyPI. Only `https://pypi.org` allowed.
- Ownership verification for PyPI: the package README (= PyPI long description) must contain `mcp-name: io.github.MarcinWolnyEU/staan-mcp` (on its own line or in an HTML comment; must be followed by whitespace/newline/`-->`, not glued to a period). It is checked against the PUBLISHED PyPI description, so the README with this line must be in the released sdist/wheel metadata BEFORE `mcp-publisher publish`.
- CI: workflow on tag `v*` with `permissions: {id-token: write, contents: read}`; install mcp-publisher via
  `curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher`, then `./mcp-publisher login github-oidc` (no secret needed) and `./mcp-publisher publish`. Optionally set version from tag: `jq --arg v "$VERSION" '.version = $v' server.json > server.tmp && mv server.tmp server.json` (also update `packages[0].version`). Order in the workflow: build and publish to PyPI (trusted publishing) FIRST, then registry publish. "invalid audience" error means mcp-publisher binary too old. Other registry pitfalls: never publish from `pull_request_target`; with PAT auth store token in a protected environment.
- Remote (HTTP) servers are listed via a `remotes` array (`{"type":"streamable-http","url":"https://..."}`) rather than `packages`; not applicable unless Staan-MCP is hosted.

## 12. Design implications and gotchas for this project

1. Use `MCPServer` from `mcp>=2.2,<3`; pin a floor of 2.2.0 and test in CI against the latest (and optionally a `--resolution lowest-direct` job).
2. Modes: `stdio` (default) -> `mcp.run()`; `http` -> `mcp.run("streamable-http", host, port, streamable_http_path, json_response=?, stateless_http=False, transport_security=...)`; `stateless` -> same with `stateless_http=True` (and `json_response=True` is sensible). SDK options are all `run()` kwargs. Do not expose SSE.
3. Because `run()` blocks and calls `anyio.run` internally, the CLI entry point must be a sync `main()`; build the `MCPServer` in a factory (`create_server(settings)`) so tests can create one per case and so the API key is read at startup (env) not import time.
4. The lifespan is the right place to create the shared `httpx.AsyncClient` (note SDK's own client uses `httpx2`; for Staan calls we may use plain `httpx`, both are fine, they are separate dependencies).
5. The `simple_response` returning a bare JSON array: see section 4 (wrapped into `{"result": [...]}` in `structured_content` unless returning `CallToolResult` or `structured_output=False`). Decide explicitly and test the exact wire shape with `Client`.
6. Tool error policy: raise `ToolError` for Staan API errors (bad request, 4xx/5xx, timeouts), never leak the API key in messages. Any other exception hides its message from the model.
7. `ctx.info()` etc. are deprecated (warning) in 2026-07-28 semantics; use stdlib `logging` (stderr) and, in pytest, set `filterwarnings = ["error"]` only after checking nothing in the SDK trips it (the SDK itself raises `MCPDeprecationWarning` for `AuthSettings` without `validate_token_resource`).
8. HTTP Host allowlist: expose CLI/env options for allowed hosts and origins; default localhost-only.
9. Windows dev machine: mcp 2.x pulls `pywin32` (Windows only); fine with uv.
10. The in-memory `Client` covers tools; use the ASGI-transport pattern (section 8) for HTTP-mode/auth/Host tests, and an actual subprocess stdio test for the entry point.
