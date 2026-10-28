# staan-mcp: design and implementation contract

This file is the contract for implementation agents. Modules are built in parallel against the
interfaces below, so keep names and signatures exactly as written. If one must change, record it
in this file in the same change. Background research is in `docs/research/`. Decisions and their
reasons are in `docs/DECISIONS.md`.

## 1. Product summary

`staan-mcp` is a Python MCP server for the Staan API (`https://api.staan.ai/v2`). It ships as a
PyPI package (`pip install staan-mcp` / `uvx staan-mcp`) with the console script `staan-mcp`, and
lives on GitHub at `MarcinWolnyEU/Staan-MCP`. It has two tools, `web_search` and `answer`, and three
transports: `stdio` (default), `http` (stateful streamable HTTP) and `stateless` (streamable HTTP
with `stateless_http=True` and `json_response=True`).

Facts established by research (do not re-litigate):
- Upstream auth is `Authorization: Bearer <STAAN_API_KEY>`. Staan has no OAuth.
- `count` must equal 10. It is not exposed and is always sent as 10.
- `offset` is in {0, 10, 20, 30}.
- There are 12 markets: `fr-fr, de-de, en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz,
  en-za, en-sg`. They are case-insensitive.
- `full_content` accepts `markdown` or `html` (case-sensitive) and returns
  `{text, format, length}`. `text` may be `""`.
- `extra_snippets` comes back as `[{chunk: str, score: float}]`, or is absent if the key's plan
  lacks it.
- POST `/search/web` returns 201 and supports `include_domains` / `exclude_domains`. Each takes at
  most 10 bare domains, and the two are mutually exclusive. A listed domain also matches its
  subdomains (verified live on 2026-10-05, market en-us: `include_domains=["python.org"]` returned
  discuss., docs., wiki. and www.python.org, and `exclude_domains=["python.org"]` returned no
  python.org host at all; the same for `wikipedia.org`). The tool descriptions say so.
- `extra_snippets=true`, which the server always sends, is what makes Staan fetch, chunk and rerank
  the pages. `full_content="none"` therefore does not skip that work: live timing on 2026-10-05
  (en-gb, 3 rounds each) was 1.0 to 1.2 s for a plain request, 1.3 to 2.2 s for
  `extra_snippets` only (what `none` sends) and 1.6 to 2.0 s with markdown as well. The real
  difference is size, about 20 KB against 115 to 170 KB (one en-gb call: 132,523 characters of page
  text, about 40k tokens), so docs say `none` shrinks the response, not that it is faster or cheaper.
- Answer is not verifiable live (403 for our keys). It is built from the documentation.
- Error bodies (see `docs/research/error-probe.md`):
  - 400: `{"message": [..] | "..", "error": "Bad Request", "statusCode": 400}`
  - 401: `{"message": "Unauthorized", "statusCode": 401}` or
    `{"code": "INVALID_CREDENTIALS", "message": "..."}`
  - 403 disabled key: `{"code": "KEY_DISABLED", "message": "API key is disabled"}`
  - 403 not allowlisted: `{"message": "Forbidden", "statusCode": 403}`
  - 404: may have an empty body
  - 429 and 5xx: undocumented

## 2. Tooling and conventions

- Python `>=3.11`. CI tests the lowest (3.11) and the latest (3.14) version on ubuntu, windows and macos.
- Use uv for everything. The build backend is `uv_build`, with a `src/` layout and `py.typed`.
  Dev tools go in PEP 735 `[dependency-groups]`.
- Runtime deps: `mcp>=2.2,<3`, `httpx2` (the SDK's HTTP client; respx does NOT work with it, so
  tests use `httpx2.MockTransport`), `pydantic>=2`, `anyio`. The optional extra `oauth` adds
  `pyjwt[crypto]>=2.14`: 2.14 added the `PyJWKClient` refresh cooldown, and older versions refetch
  the JWKS for every forged token with an unknown `kid`. The build backend is pinned exactly
  (`uv_build==0.12.21`) because the release build runs with a signing token in the workflow.
- Quality gates. All must pass locally and in CI:
  - `ruff check` (`select = ["ALL"]`, with documented ignores) and `ruff format --check`
  - `mypy --strict` (`src`, `tests`, `scripts`)
  - `pylint src tests scripts` (10.00/10, or fixed and documented disables)
  - `xenon --max-absolute B --max-modules A --max-average A src` (radon)
  - `pytest` with 100% line and branch coverage (`fail_under = 100`) of `staan_mcp` and of
    `scripts/build_zipapp.py`; `stdio_server.py` is covered like every other module
  - `mutmut` (Linux/Docker/CI only, gated on `mutants/mutmut-cicd-stats.json`)
  - `uv build`, plus `twine check` / `check-wheel-contents`, run from the locked `dist`
    dependency group (`uv run --only-group dist ...`), not through an unpinned `uvx`
- Tests use pytest with anyio's plugin (`@pytest.mark.anyio`), hypothesis for pure functions, and
  JSON fixtures in `tests/fixtures/`. Search fixtures are sanitised from real responses in `.tmp/`.
  Answer fixtures are written from the docs.
- Live tests live in `tests/live/` with the marker `live`. They are skipped unless `STAAN_API_KEY`
  is set and `--run-live` is passed. They are excluded from coverage and never run in CI by
  default.
- Logging goes to stderr only (stdout belongs to stdio JSON-RPC). Never log or echo key values or
  `Authorization` headers.

## 3. Package layout

```
src/staan_mcp/
  __init__.py      # __version__ via importlib.metadata; __all__
  __main__.py      # from staan_mcp.cli import main; raise SystemExit(main())
  py.typed
  markets.py       # market constants + wildcard expansion               (stdlib only)
  domains.py       # domain normalisation (Answer filter, include/exclude_domains) (stdlib only)
  errors.py        # exception hierarchy + upstream error mapping         (stdlib only)
  config.py        # Settings (env), HttpConfig, AuthConfig               (stdlib only)
  formatting.py    # search simplification + interleave/dedupe; answer citations -> GFM footnotes (stdlib only)
  contract.py      # server name, instructions, tool texts, limits, tool JSON schemas (stdlib only)
  upstream.py      # request bodies, paths, headers, retry/backoff constants and messages (stdlib only)
  payloads.py      # tool results from raw upstream responses; failure aggregation (stdlib only)
  sources.py       # validation of caller-supplied answer sources         (stdlib only)
  ratelimit.py     # sliding-window request rate limiter, clock-injectable (stdlib only)
  client.py        # async StaanClient (httpx2), retries
  auth.py          # optional OAuth resource-server gate (JWT/JWKS, introspection verifiers)
  server.py        # create_server(): MCPServer with tools web_search + answer
  cli.py           # argparse entry point, transport selection, startup safety rules
  stdio_server.py  # standalone stdio server, Python 3.11+, run by path with nothing installed;
                   # urllib transport + JSON-RPC loop + argument checks; reuses the stdlib-only modules above
scripts/
  build_zipapp.py  # builds staan-mcp-stdio.pyz (stdlib only; not part of the installed package)
tests/
  conftest.py, fixtures/*.json, staan_fakes.py (httpx2 fake), local_staan.py (loopback fake that
  stamps arrivals), rpc_process.py (drives a server process), rate_limit_helpers.py,
  stdio_helpers.py (in-process stdio server fixtures), repo_files.py (repository file readers)
  unit/test_markets.py, test_domains.py, test_errors.py, test_config.py, test_client.py,
       test_formatting.py, test_auth.py, test_cli.py, test_cli_text.py, test_package.py,
       test_fixtures.py, test_conftest.py, test_mutation_shared.py (shared stdlib modules),
       test_ratelimit.py, test_client_rate_limit.py, test_stdio_arguments.py,
       test_stdio_server.py, test_stdio_transport.py, test_stdio_rate_limit.py,
       test_stdio_shutdown.py
  integration/test_server_tools.py (in-memory mcp.Client), test_server_contract.py,
       test_answer_tool.py, test_http_app.py (ASGI over httpx2), test_http_process.py, test_stdio.py,
       test_stdio_parity.py (drift guard against the package), test_stdio_wire.py (every kind of
       JSON-RPC line, both processes), test_stdio_standalone.py (no third party, lone-file message),
       test_stdio_process_exit.py, test_rate_limit_package.py, test_rate_limit_stdio.py
  repo/ (packaging and CI files: test_dependencies_and_releases.py, test_release_workflow.py,
       test_workflow_hygiene.py, VERSION agreement in test_stdio_version.py, test_build_zipapp.py,
       test_stdio_bundle.py)
  live/test_live.py
```

## 4. Module contracts

### 4.1 `markets.py`
```python
MARKETS: Final[tuple[str, ...]] = ("fr-fr","de-de","en-us","en-gb","en-fr","en-ca","en-au","en-in","en-ie","en-nz","en-za","en-sg")
DEFAULT_MARKET: Final = "en-gb"
class InvalidMarketError(ValueError): ...
def expand_markets(spec: str | Sequence[str]) -> list[str]:
```
- Accepts one string or a sequence of strings. Each item is stripped and lowercased.
- An item containing `*`, `?` or `[` is a pattern. Expand it with `fnmatch.fnmatchcase` against
  `MARKETS` in `MARKETS` order. Any other item must be an exact member.
- Examples: `en-*` and `en*` give the 10 `en-` markets, `*-fr` gives `fr-fr, en-fr`, `*` gives all
  12.
- Result: deduplicated, keeping first-seen order.
- These raise `InvalidMarketError` with a message that lists the valid markets and states the
  wildcard syntax:
  - an empty spec, an empty list, or an empty string item
  - an unknown exact code
  - a pattern that matches nothing

### 4.2 `domains.py`
```python
MAX_DOMAINS: Final = 10
class InvalidDomainError(ValueError): ...
def normalize_domain(value: str) -> str
def normalize_domains(values: Sequence[str]) -> list[str]
```
- `normalize_domain` does the following, in order:
  1. Strip whitespace.
  2. Remove the URL scheme (`^[a-zA-Z][a-zA-Z0-9+.-]*://`) and a leading `//`.
  3. Drop everything from the first `/`, `?` or `#`.
  4. Drop `userinfo@`.
  5. Drop the `:port`.
  6. Lowercase.
  7. Strip a trailing `.`.
  8. Remove one leading `www.`.
- After those steps the value must contain at least one dot, use labels of `[a-z0-9-]` (IDN input
  is converted with the `idna` codec to punycode), have no empty labels, and have no label that
  starts or ends with `-`. Otherwise raise `InvalidDomainError`.
- Examples: `"https://www.Example.com:443/a?b"` gives `"example.com"`,
  `"www.wikipedia.org"` gives `"wikipedia.org"`, and `"en.wikipedia.org"` stays
  `"en.wikipedia.org"`.
- `normalize_domains` normalises each item and dedupes in order. It raises if the result has more
  than `MAX_DOMAINS` entries. (The planned "empty result for non-empty input" guard was dropped: it
  is unreachable, because every item either raises or yields a domain.)
- `normalize_domain` removes only one `www.` per call, so `www.www.a.com` needs two passes. The
  property "idempotent" therefore holds for every result that does not start with `www.`.
- The `:port` step only drops `:` followed by digits (or nothing), so `a.com:abc` stays invalid.

### 4.3 `errors.py`
```python
class StaanMCPError(Exception): """Base; str(e) is a safe, user-facing message (never contains secrets)."""
class ConfigurationError(StaanMCPError)        # e.g. missing STAAN_API_KEY
class StaanAPIError(StaanMCPError):            # attrs: status_code: int | None, code: str | None, endpoint: str
class StaanAuthenticationError(StaanAPIError)  # 401 (both body shapes)
class StaanKeyDisabledError(StaanAPIError)     # 403 code=KEY_DISABLED
class StaanForbiddenError(StaanAPIError)       # 403 other. Answer: "not enabled for this API key (invitation-only, contact support@staan.ai)"
class StaanValidationError(StaanAPIError)      # 400/422: messages joined with "; "
class StaanRateLimitError(StaanAPIError)       # 429 (after retries)
class StaanServerError(StaanAPIError)          # 5xx (after retries) and other unexpected statuses
class StaanTransportError(StaanAPIError)       # network/timeout (after retries), status_code None
class StaanResponseError(StaanAPIError)        # 2xx but body not a JSON object / wrong shape
FATAL_ERRORS: Final = (StaanAuthenticationError, StaanKeyDisabledError, StaanForbiddenError)
def error_from_response(status_code: int, body: bytes, *, endpoint: Literal["search", "answer"]) -> StaanAPIError
```
- Constructor (added during implementation): `StaanAPIError(message: str, *, status_code: int | None
  = None, code: str | None = None, endpoint: str = "")`. All subclasses share it, so the client
  builds transport and response-shape errors directly, for example
  `StaanTransportError("...", endpoint="search")`.
- Mapping (`error_from_response`): 401 -> `StaanAuthenticationError`; 403 with
  `code == "KEY_DISABLED"` -> `StaanKeyDisabledError`; other 403 -> `StaanForbiddenError` (answer
  endpoint: "not enabled for this API key (invitation-only, contact support@staan.ai)"); 400 and 422
  -> `StaanValidationError`; 429 -> `StaanRateLimitError`; everything else (5xx, 404, ...) ->
  `StaanServerError`. 5xx messages never echo upstream text.
- Parse defensively. The body may be empty, invalid JSON, or JSON without `message`. `message`
  may be a string or a list.
- Never include the raw body in the message beyond the `message` field's text. Cap that text at 500
  characters.

### 4.4 `config.py`
```python
DEFAULT_BASE_URL: Final = "https://api.staan.ai/v2"
@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str | None          # repr must NOT show it (field(repr=False))
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 30.0        # seconds per attempt; full_content fetches take a few s, answers longer
    max_retries: int = 2         # retries for 429/5xx/connection errors (never for read timeouts)
    deadline: float = 50.0       # seconds for the whole call, retries and waits included
    max_content_chars: int = 0   # per-result full_content limit; 0 = unlimited
    rate_limit: float = 20.0     # upstream requests started per second, all calls together; 0 = off
    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings"
```
- Env vars: `STAAN_API_KEY` (stripped; empty means None), `STAAN_BASE_URL`, `STAAN_TIMEOUT`,
  `STAAN_DEADLINE`, `STAAN_MAX_RETRIES`, `STAAN_RATE_LIMIT`, `STAAN_MCP_MAX_CONTENT_CHARS`.
- `STAAN_API_KEY` must consist of visible ASCII characters (0x21 to 0x7E) only. A key with a pasted
  smart quote, an embedded space or a control character is a `ConfigurationError` naming only the
  variable, never the value: it could not be sent in a header (httpx2 would raise
  `UnicodeEncodeError` on the first request) and the failure used to surface as an opaque
  `ExceptionGroup`.
- `STAAN_TIMEOUT` and `STAAN_DEADLINE` are finite numbers greater than 0. `STAAN_MCP_MAX_CONTENT_CHARS`
  is `0` (no limit, the default) or at least 200. The default is deliberately "off": the server
  never shortens pages unless the operator asks. `full_content` defaults to `none` (changed from
  `markdown` on 2026-10-11, because one single-market call with `markdown` is about 115 to 170
  thousand characters, over the 25k-token default tool-output limit of some clients), so the cap
  only matters when a caller asks for `markdown` or `html`.
- `STAAN_RATE_LIMIT` is requests per second: a finite number, `0` (limiter off) or greater (see
  4.11). Values above Staan's cap of 20 are accepted, since a plan may allow more.
- Blank values count as unset. `STAAN_BASE_URL` has trailing slashes removed. A missing API key is
  not an error in `from_env`; the client raises it on first use.
- `Settings` and `AuthConfig` validate only in `from_env`, not on direct construction.
- Invalid numbers raise `ConfigurationError`. So do `timeout <= 0`, `max_retries` outside 0..10,
  and a non-https `base_url` (unless the host is loopback).
- URL variables (`STAAN_BASE_URL` and every `STAAN_MCP_AUTH_*_URL`) must be absolute http(s) URLs
  with a host and a valid port (`urlsplit(...).port` is evaluated, so `https://host:abc/` is a
  startup error), and must not carry userinfo (`user:password@`). Error messages name only the
  variable and never repeat the value, because a URL can hold credentials.

```python
Transport = Literal["stdio", "http", "stateless"]
@dataclass(frozen=True, slots=True)
class AuthConfig:      # OAuth resource-server gate (http/stateless only)
    issuer_url: str
    resource_url: str               # canonical MCP URL, e.g. https://mcp.example.com/mcp ; also the expected audience
    required_scopes: tuple[str, ...] = ()
    jwks_url: str | None = None     # exactly one of jwks_url / introspection_url
    introspection_url: str | None = None
    client_id: str | None = None    # for introspection (HTTP Basic client auth)
    client_secret: str | None = None  # repr=False
    algorithms: tuple[str, ...] = ("RS256", "ES256")  # never "none"
    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AuthConfig | None"   # None when no STAAN_MCP_AUTH_* set
```
- Env vars:
  - `STAAN_MCP_AUTH_ISSUER_URL`, `STAAN_MCP_AUTH_RESOURCE_URL`
  - `STAAN_MCP_AUTH_SCOPES` (space- or comma-separated)
  - `STAAN_MCP_AUTH_JWKS_URL`, `STAAN_MCP_AUTH_INTROSPECTION_URL`
  - `STAAN_MCP_AUTH_CLIENT_ID`, `STAAN_MCP_AUTH_CLIENT_SECRET`
  - `STAAN_MCP_AUTH_ALGORITHMS`
- Partial config raises `ConfigurationError`. Validation rules:
  - Issuer and resource URLs are required.
  - Exactly one of `jwks_url` / `introspection_url` must be set.
  - Introspection needs `client_id` and `client_secret`.
  - All URLs must be https unless the host is loopback.
  - `none` is never allowed as an algorithm.

```python
@dataclass(frozen=True, slots=True)
class HttpConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    insecure_no_auth: bool = False
def is_loopback(host: str) -> bool   # 127.0.0.0/8, ::1, "localhost"
```

### 4.5 `client.py`
```python
SEARCH_COUNT: Final = 10  # defined in upstream.py, with the request bodies and retry constants
FullContentFormat = Literal["markdown", "html"]  # also in upstream.py
class StaanClient:
    def __init__(self, settings: Settings, *, transport: httpx2.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
                 limiter: RateLimiter | None = None) -> None   # default: RateLimiter(settings.rate_limit)
    async def __aenter__(self) -> Self ; async def __aexit__(...) -> None ; async def aclose(self) -> None
    async def search_web(self, *, query: str, market: str, offset: int = 0,
                         full_content: FullContentFormat | None = None,
                         include_domains: Sequence[str] | None = None,
                         exclude_domains: Sequence[str] | None = None) -> dict[str, Any]
    async def answer(self, *, query: str, language: Literal["en","fr"] = "en", mode: Literal["short","long"] = "short",
                     filter_domain: str | None = None, sources: Sequence[Mapping[str, str]] | None = None,
                     sources_mode: Literal["search","context"] = "search") -> dict[str, Any]
```
- Every call raises `ConfigurationError("STAAN_API_KEY is not set ...")` before any network I/O
  when `settings.api_key` is None.
- Search is always a `POST {base}/search/web` with this JSON body:
  - always: `q`, `market`, `count: 10`, `offset`, `extra_snippets: true`
  - `full_content` only when it is not None
  - `include_domains` / `exclude_domains` only when non-empty
  - never `min_score` or `max_snippets`
- Answer is `POST {base}/answer` with this body:
  - always: `query`, `language`, `mode`, `stream: false`, `markdown: true`
  - `filter` when set, `sources` when set
  - `sources_mode` only when `sources` is set
  - never `history`, `related_queries` or `query_rewrite`
- Headers: `Authorization: Bearer <key>`, `Accept: application/json`, and
  `User-Agent: staan-mcp/<version> (+https://github.com/MarcinWolnyEU/Staan-MCP)`.
- Success statuses are 200 and 201. The body must be a JSON object, otherwise raise
  `StaanResponseError`.
- Retries (`max_retries`) apply to 429, 500, 502, 503, 504 and to `httpx2.TransportError`, except
  `ReadTimeout` and `WriteTimeout` (see below):
  - Backoff is `min(2 ** attempt * 0.5, 8)` seconds, plus a `Retry-After` of up to 30 s when
    present and numeric. Use the injected `sleep`.
  - No retry on other 4xx.
  - After the retries run out, raise the mapped error.
- Implementation notes (added during implementation):
  - `filter_domain` and `sources` count as "set" when truthy, so `""` and `[]` are omitted.
  - A falsy `api_key` (`None` or `""`) raises `ConfigurationError`.
  - Redirects are never followed (the `Authorization` header must not leave the API host). A 3xx
    maps to `StaanServerError`.
  - Retryable statuses are exactly 429, 500, 502, 503 and 504. Other 5xx, such as 501, are not
    retried.
  - `httpx2.TransportError` is retried and ends as `StaanTransportError`, except `ReadTimeout` and
    `WriteTimeout`: the request was (or may have been) sent and every request is billable, so a
    slow answer is reported at once instead of being paid for again. Connect and pool timeouts
    are still retried (nothing was sent). Other `httpx2.RequestError`s (for example
    `DecodingError`) become `StaanTransportError` immediately, without a retry. The original
    exception is set as `__cause__`; its text is never copied.
  - The whole call (attempts, backoff and `Retry-After` waits) runs under `anyio.fail_after(
    settings.deadline)` (default 50 s, below the 60 s many MCP clients allow). On expiry it raises
    `StaanTransportError("The call to Staan did not finish within N s.")`. With the old defaults
    (30 s, 2 retries) one failing call could take about 91 s and send three billable requests.
    Jitter and clamping `Retry-After` to the remaining budget were considered and not done: the
    deadline already bounds the wait.
  - The response body is read in a stream and abandoned once it passes `MAX_RESPONSE_BYTES`
    (32 MiB), raising `StaanResponseError` without a retry; the buffered body is then rebuilt as an
    `httpx2.Response` without the encoding and length headers.
  - `httpx2.InvalidURL` and `UnicodeError` (raised while the request is built: a bad port in
    `STAAN_BASE_URL`, a non-ASCII key) become a `ConfigurationError` naming `STAAN_BASE_URL` and
    `STAAN_API_KEY` (never the value). It is fatal for the call, so a multi-market search reports
    it once instead of failing with an `ExceptionGroup`.
  - `Retry-After` is ignored if it is not a finite number >= 0 (HTTP-dates are ignored).
  - Retries are logged at DEBUG on the `staan_mcp.client` logger, without secrets.
  - Every attempt, the first and each retry, takes a slot from the client's rate limiter right
    before it is sent (4.11).

### 4.6 `formatting.py`
```python
class SimpleSearchResult(TypedDict):
    title: str; url: str; snippet: str; full_content: NotRequired[str]
SNIPPET_SEPARATOR: Final = " (...) "
def simplify_result(result: Mapping[str, Any], *, max_content_chars: int = 0) -> SimpleSearchResult
def truncate_text(text: str, limit: int) -> str
def interleave_results(per_market: Sequence[Sequence[Mapping[str, Any]]]) -> list[Mapping[str, Any]]
def citations_to_gfm(answer: str, citations: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]]) -> str
```
- `simplify_result`:
  - `snippet` is `SNIPPET_SEPARATOR.join([s for s in [snippet, *chunks] if s.strip()])`. Each chunk
    comes from an `extra_snippets` item that is a dict with a `chunk` str, or a plain str. Other
    types are skipped.
  - `full_content` is set only when the upstream `full_content` is a dict whose `text` is a
    non-empty string (or is itself a non-empty str). With `max_content_chars > 0` it goes through
    `truncate_text`; snippets and extra snippets are never cut.
  - `truncate_text(text, limit)` returns `text` when `limit <= 0` or it fits. Otherwise it cuts
    `text[:limit]` at the last blank line, else the last line break or space, provided that is not
    in the first half (else a hard cut at `limit`), strips trailing whitespace and appends
    `"\n\n[... truncated: K of N characters shown ...]"`.
  - Missing `title` or `snippet` becomes `""`.
- `interleave_results` merges round-robin by rank: rank 0 of market 1, rank 0 of market 2, ...,
  then rank 1, and so on. It dedupes by `url` and the first occurrence wins. Items without a str
  `url` are dropped.
- `citations_to_gfm` follows `docs/research/gaps.md` §B5 plus two placement rules:
  1. Map `source_id` to a URL by `str(id)` from `sources`. Only when **no** source carries a usable
     id (`urls_by_id` is empty), a decimal id `n` with `1 <= n <= len(sources)` and at most 18
     digits falls back to `sources[n-1]`. When ids exist, an unknown id is ignored: with ids
     `1` and `3`, a citation to `2` used to cite the `3` page, and a wrong citation is worse than
     a dropped one. Ids are a `str` or an `int` of at most 64 bits (`str()` of a huge int would
     raise `ValueError`); others are ignored.
  2. The position unit is Python code points. Isolate it in
     `_index_for(answer: str, position: object) -> int | None`: non-int positions or bools give
     None (citation ignored), and positions are clamped to `[0, len(answer)]`.
  3. Placement normalisation:
     - If the index falls strictly inside a word (alphanumeric on both sides), advance to the end
       of that word.
     - If the characters just before the index are whitespace, move back over them so the marker
       attaches to the preceding text.
  4. Number footnotes in text order of first appearance (ties keep input order). A repeated source
     reuses its number.
  5. Several markers at one index join as `[^1][^2]`, deduped by number.
  6. Insert from the highest index down.
  7. Append `"\n\n" + "\n".join(f"[^{n}]: {url}")` for cited sources only, in number order.
  8. If nothing valid is cited, return `answer` unchanged.
  9. Only a safe source URL is ever printed: at most 2048 characters, no whitespace or control
     characters (a line break would inject Markdown after the definitions), a scheme of `http` or
     `https` and a host. Other sources behave as if they had no URL. Every definition line
     therefore matches `^\[\^\d+\]: \S+$` (a hypothesis test checks it).
  10. Markdown blocks. The answer is scanned for fenced code blocks (backtick or tilde fences,
      closed by a fence of the same character, at least as long, without an info string; an
      unclosed fence runs to the end) and tables (runs of at least two lines that start with `|`).
      After the word and whitespace normalisation, an index that falls inside such a block, at
      its end or at its first character moves to the end of the text before the block (repeatedly,
      for adjacent blocks). If nothing but whitespace precedes the block, the citation is dropped.
      Without this, `` ```[^1] `` is no longer a closing fence and swallows the rest of the answer
      including the definitions, `| 1 | 2 |[^1]` loses the marker, and a marker in a fence is
      printed literally. Link destinations and inline code spans are not handled (optional in the
      review; the API's real position semantics are unverified because Answer returns 403).

### 4.7 `auth.py` (optional OAuth resource-server gate)
```python
class OAuthDependencyError(ConfigurationError)   # pyjwt missing -> "install staan-mcp[oauth]"
class JWTTokenVerifier:            # implements mcp.server.auth.provider.TokenVerifier
    def __init__(self, config: AuthConfig, *, jwk_client: JWKClient | None = None) -> None  # JWKClient: Protocol with get_signing_key_from_jwt(token) -> obj with .key
    async def verify_token(self, token: str) -> AccessToken | None
class IntrospectionTokenVerifier:
    def __init__(self, config: AuthConfig, *, transport: httpx2.AsyncBaseTransport | None = None) -> None
    async def verify_token(self, token: str) -> AccessToken | None
def build_token_verifier(config: AuthConfig) -> TokenVerifier
def build_auth_settings(config: AuthConfig) -> AuthSettings   # issuer_url, resource_server_url=resource_url, required_scopes, validate_token_resource=True
```
- JWT: PyJWT with `PyJWKClient(jwks_url, timeout=10, cooldown_duration=30, lifespan=300)`.
  `PyJWKClient` is synchronous, so call it via `anyio.to_thread.run_sync`. The cooldown (PyJWT 2.14+)
  stops tokens with an unknown `kid` from forcing a JWKS fetch each (200 forged tokens caused 201
  fetches on PyJWT 2.10 to 2.13 and one fetch on 2.14+), and the timeout keeps a slow identity
  provider from pinning worker threads for PyJWT's default 30 s. Decode with:
  - `algorithms=config.algorithms`
  - `audience=` the equivalent spellings of `config.resource_url`
  - `issuer=` the equivalent spellings of `config.issuer_url`
  - `options={"require": ["exp", "iss", "aud"]}`
- Equivalent spellings (`_equivalent_urls`): the SDK publishes an origin-only URL with a trailing
  slash (`AnyHttpUrl` normalisation), in `resource` and `authorization_servers` of the
  protected-resource metadata, while the configured string, and usually the AS's `iss`, have none.
  A client that follows RFC 9728/8707 then sends `aud=https://host/`, which the exact comparison
  rejected. For an origin-only URL both spellings are accepted (audience and issuer, in both
  verifiers); a URL with a path is matched exactly. `AccessToken.resource` stays the configured
  string (the SDK compares it ignoring a trailing slash).
- Introspection (RFC 7662): POST `token=<t>` with HTTP Basic client auth over one
  `httpx2.AsyncClient` shared for the verifier's lifetime (closed by the server lifespan through
  `IntrospectionTokenVerifier.aclose()`). A token longer than 4096 characters or not visible ASCII
  (`[\x21-\x7e]+`) is rejected without a request. Require:
  - `active` is true
  - `aud` (a str or a list) contains `resource_url` (or its equivalent spelling)
  - `exp` is in the future, if present (a non-finite `exp`, JSON `Infinity` or `NaN`, is
    unusable and rejects the token; it used to raise `OverflowError` out of `verify_token`)
  - `iss` matches, if present (a non-string `iss` or `aud` item rejects, it never raises)
  - No cache, negative cache or concurrency limit: they were optional in the review, and
    per-request introspection is normal under RFC 7662.
- Both verifiers:
  - Scopes come from `scope` (space-separated) or `scp` (a list).
  - Return `AccessToken(token=..., client_id=client_id or azp or sub or "unknown", scopes=...,
    expires_at=exp, resource=config.resource_url, subject=sub, claims={"iss": iss})`. The SDK binds
    a stateful session to `principal_components` = `(client_id, claims["iss"], subject)`; without
    `subject` and `iss`, two users of one client counted as one principal and one could replay the
    other's `mcp-session-id`. `subject` is the `sub` claim when it is a non-empty string, `iss` is
    the token's (introspection: the configured issuer when the response has none). Both fields
    exist since mcp 2.2.
  - On any failure, return `None` and log at debug level without the token. Never raise.
- Never forward the inbound token upstream: the token-passthrough ban.

### 4.8 `server.py`
```python
SERVER_NAME: Final = "staan"  # defined in contract.py
def create_server(settings: Settings, *, auth: AuthConfig | None = None,
                  client_factory: Callable[[Settings], StaanClient] = StaanClient) -> MCPServer
```
- `MCPServer(SERVER_NAME, instructions=..., lifespan=..., token_verifier=..., auth=...)`. The
  verifier and auth arguments are passed only when `auth` is set. The lifespan opens one
  `StaanClient` for the server's lifetime, hands the tools `_AppState(client, max_content_chars)`
  and closes an `IntrospectionTokenVerifier` on shutdown (an `AsyncExitStack`).
- Tool `web_search`. It is read-only and open-world:
  `ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True,
  destructive_hint=False)`.
  - Parameters:
    - `query: Annotated[str, Field(min_length=1, max_length=400)]`
    - `market: str | list[str] = "en-gb"`, described with the full market list, the wildcard
      examples, and the note that each market is one billable request
    - `full_content: Literal["markdown", "html", "none"] = "markdown"`. The descriptions say that
      `none` mainly shrinks the response and token use, that the server always requests extra
      snippets (so cost and latency barely change), that markdown/html add little latency but a
      lot of text (over 100,000 characters per call, HTML far more), and that the operator may
      cap pages with `STAAN_MCP_MAX_CONTENT_CHARS`. They never call `none` faster or cheaper.
    - `offset: Literal[0, 10, 20, 30] = 0`
    - `include_domains: list[str] | None = None` (a domain also covers its subdomains)
    - `exclude_domains: list[str] | None = None` (subdomains included)
    - `simple_response: bool = True`
    - `ctx: Context`
  - Validation:
    - Markets are expanded with `expand_markets`. Domains are normalised with
      `normalize_domains`.
    - Including and excluding domains together raises `ToolError`.
    - `InvalidMarketError` and `InvalidDomainError` become `ToolError`.
  - Run one `search_web` per market concurrently with an anyio task group, keeping market order.
  - If any market raises a `FATAL_ERRORS` instance, raise `ToolError(str(err))` immediately.
  - Other `StaanAPIError`s are recorded per market. If every market failed, raise `ToolError` with
    the combined messages. Otherwise log a warning naming the failed markets.
  - The tool returns `CallToolResult(content=[TextContent(type="text", text=json.dumps(payload,
    ensure_ascii=False))])` with no structured content:
    - `simple_response=True`: `payload` is a JSON array of `SimpleSearchResult`, built with
      `interleave_results` then `simplify_result(max_content_chars=...)`.
    - `simple_response=False`: `payload` is a JSON array with one entry per market, in market
      order. A successful market's entry is the raw response (each `full_content.text` is cut
      to `max_content_chars` in place when that is set; `length` keeps the upstream value). A
      failed market's entry is `{"market": m, "error": msg}`.
- Tool `answer`. Same annotations, but `idempotent_hint=False`.
  - Parameters:
    - `query: Annotated[str, Field(min_length=1, max_length=2000)]`
    - `filter: str | None = None`. Normalised with `normalize_domain`. An invalid value raises
      `ToolError`. An empty or whitespace-only string means no filter. The parameter name is
      `filter`; use `# noqa: A002` or an alias.
    - `language: Literal["en", "fr"] = "en"`
    - `mode: Literal["short", "long"] = "short"`
    - `sources: list[AnswerSource] | None = None` (max 10)
    - `sources_mode: Literal["search", "context"] = "search"`
    - `simple_response: bool = True`
    - `ctx: Context`
  - `AnswerSource` is a pydantic model with optional fields:
    - `url: str | None`
    - `title: str | None` (max 500)
    - `content: str | None` (max 50,000)
    - A validator requires `url` or `content`.
    - `url` is validated (Staan fetches it, so this is the SSRF guard): stripped (blank means
      absent), at most 2048 characters, no whitespace or control characters, a scheme of `http` or
      `https` with a host and a valid port, and not `localhost` or a literal IP that is not global
      (private, loopback, link-local, reserved, unspecified, CGNAT, multicast, IPv4-mapped IPv6 of
      those). A pydantic `ValueError`, so the SDK reports it as an invalid-input tool error before
      any request. It is a filter on the literal URL: names that resolve to internal addresses are
      not caught.
  - Return type is `Annotated[CallToolResult, AnswerOutput]`, with
    `AnswerOutput(BaseModel, extra="allow")` and a required `answer: str`, so `outputSchema`
    declares `{"answer": string}` (previously the open `answerDictOutput` object) and the SDK
    validates it. The result carries `structured_content` and a compact JSON text block of the
    same payload (the SDK used to pretty-print it):
    - simple: `{"answer": citations_to_gfm(answer, citations, sources)}`
    - full: the raw upstream dict
  - A missing or non-str `answer` field raises `ToolError`, in both modes (the schema requires
    `answer`; previously `simple_response=False` returned the raw dict as is).
- All `StaanMCPError`s, including `ConfigurationError` for a missing key, become
  `ToolError(str(e))`. Unexpected exceptions propagate. The SDK reports them generically, and that
  is acceptable.
- Server `instructions` are short guidance for the model on when to use each tool.
- Implementation notes (added during implementation):
  - `create_server` returns `MCPServer[_AppState]` (the lifespan yields a private dataclass with the
    shared client). The tools are module-level coroutine functions registered with
    `server.add_tool(...)`, so each tool's description and annotations are explicit.
  - Every tool parameter after `query` is keyword-only, and `ctx` is the last keyword-only
    parameter. The JSON schema is unaffected (`ctx` is not part of it).
  - `web_search` is registered with `structured_output=False`. `answer` has the SDK's generated
    output schema for `dict[str, Any]`.
  - A Staan search response keeps its results in `web.results`. A malformed body gives no results.
  - A `FATAL_ERRORS` instance or a `ConfigurationError` from any market cancels the markets still
    running and becomes `ToolError(str(err))` without a market prefix. If every market failed, the
    message is `"<market>: <error>"` entries joined with `"; "` (also for a single market).
  - `AnswerSource` also rejects a `url`/`content` that is blank (whitespace only). A blank `url`
    next to a `content` is dropped, not forwarded.
  - A `ConfigurationError` from any market (including the unbuildable-request error of
    `StaanClient`) is fatal and reported once, as a plain `ToolError`.
  - The schema descriptions (tool and parameters) are written for LLM consumers and are asserted
    by `tests/integration/test_server_tools.py`.

### 4.9 `cli.py`
```python
def build_parser() -> argparse.ArgumentParser
def main(argv: Sequence[str] | None = None) -> int
def run_server(server: MCPServer, transport: Transport, http: HttpConfig) -> None   # thin, monkeypatchable
```
- Options, each with a `STAAN_MCP_*` env default:
  - `--transport {stdio,http,stateless}` (`STAAN_MCP_TRANSPORT`, default stdio)
  - `--host` (`STAAN_MCP_HOST`, default 127.0.0.1)
  - `--port` (`STAAN_MCP_PORT`, default 8000)
  - `--path` (`STAAN_MCP_PATH`, default /mcp)
  - `--allowed-host` (repeatable; `STAAN_MCP_ALLOWED_HOSTS`, comma-separated)
  - `--allowed-origin` (same pattern, with `STAAN_MCP_ALLOWED_ORIGINS`)
  - `--insecure-no-auth`
  - `--log-level` (`STAAN_MCP_LOG_LEVEL`, default WARNING)
  - `--version`
- Startup rules. A violation prints a clear message to stderr and returns exit code 2:
  - `AuthConfig` with the stdio transport is rejected (OAuth is for HTTP only).
  - A non-loopback HTTP host needs `AuthConfig` or `--insecure-no-auth`.
  - A non-loopback HTTP host needs at least one `--allowed-host`, passed to
    `TransportSecuritySettings`.
  - A missing `STAAN_API_KEY` is only a stderr warning at startup, not an error. MCP clients and
    registries must be able to list tools without a key. The tools then fail with a clear
    message.
- Transport behaviour:
  - `http` runs `server.run("streamable-http", host=..., port=..., streamable_http_path=...,
    transport_security=...)`.
  - `stateless` runs the same with `stateless_http=True, json_response=True`.
  - On a loopback host with no allowed hosts given, pass explicit `TransportSecuritySettings`
    (hosts `127.0.0.1:*`, `localhost:*`, `[::1]:*`; origins `http://127.0.0.1:*`,
    `http://localhost:*`, `http://[::1]:*`). The SDK only auto-enables protection when the host is
    exactly `127.0.0.1`, `localhost` or `::1`, while `is_loopback` accepts any `127.0.0.0/8`
    address, other cases of `localhost`, `[::1]` and `0:0:0:0:0:0:0:1`: with `transport_security=
    None` those spellings started without protection and answered a rebound `Host` with 200.
- `ConfigurationError` from `Settings.from_env` or `AuthConfig.from_env` prints the message and
  returns 2.
- Implementation notes (added during implementation):
  - `main` never raises `SystemExit`: argparse exits (`--version`, `--help`, usage errors) are turned
    into return codes (0 or 2). Env defaults are validated like flag values (a bad
    `STAAN_MCP_PORT` is a usage error).
  - Flags replace the environment lists (`--allowed-host` replaces `STAAN_MCP_ALLOWED_HOSTS`).
    `--insecure-no-auth` deliberately has no environment variable.
  - `--transport` and `--log-level` are case-insensitive. `--port` is 1..65535 and `--path` must
    start with `/`.
  - `--log-level` configures the root logger (stderr) before the server is created, so the SDK's own
    `basicConfig` call is a no-op.
  - `--log-level` is also copied to `server.settings.log_level` before `run_server`, because the SDK
    passes it to uvicorn (so `WARNING` hides the per-request access log). `create_server` is unchanged.
  - The missing-key warning is a `logging` warning on `staan_mcp.cli`, so `--log-level ERROR` hides it.
  - `transport_security` is `None` only for a non-loopback bind with neither hosts nor origins
    configured, which the startup rules refuse before it can happen. With origins but no hosts, the
    loopback hosts stay allowed. With hosts but no origins, no `Origin` is accepted (unchanged);
    only when nothing is configured on a loopback bind are the loopback origins added. Host
    patterns are passed through exactly as given (no automatic `:*`).
  - `--host` is normalised (`strip`, `lower`, unbracket) before it reaches `HttpConfig`, uvicorn and
    `is_loopback`, so `[::1]` and ` LOCALHOST ` work as expected.
  - A `ConfigurationError` from `create_server` (for example a missing `pyjwt`) also exits with 2.

### 4.10 Shared modules and `stdio_server.py`

There are exactly three supported ways to run the server:

1. Installed: `pip install staan-mcp` / `uvx staan-mcp`. The full package (`mcp`, `httpx2`,
   `pydantic`, `anyio`, optional OAuth), stdio and both HTTP transports.
2. Download and run: a source checkout and `python src/staan_mcp/stdio_server.py`, with a plain
   Python 3.11+ (the package's own `requires-python`), nothing installed, stdio only.
3. The single-file bundle `staan-mcp-stdio.pyz` (a release asset): the same `stdio_server.py` and
   the modules it imports, in one zip application, run with `python staan-mcp-stdio.pyz`. Same
   requirements and limits as way 2.

Everything that decides what a client sees is written once, in modules that import only the
standard library, and all three import it:

- `markets.py`, `domains.py`, `errors.py`, `config.py`, `formatting.py`: as described above.
  `errors.is_fatal` and `domains.normalize_domain_filters` were added for sharing.
- `contract.py`: `SERVER_NAME`, `INSTRUCTIONS`, the tool titles, descriptions and every parameter
  description, the limits (query lengths, source counts and sizes), the enumerated choices and
  `TOOL_LIST`, the `tools/list` result of the standalone server (input and output JSON schemas
  and annotations, written out by hand). The package builds the same schemas with pydantic;
  `tests/integration/test_stdio_parity.py` compares them field by field.
- `upstream.py`: the request bodies (`search_body`, `answer_body`), the paths, the success and
  retry statuses, `SEARCH_COUNT`, `MAX_RESPONSE_BYTES`, the backoff and `Retry-After` helpers, the
  request headers, and the wording of transport failures. `StaanClient` and the standalone `urllib`
  transport both use them.
- `payloads.py`: `search_payload` (simple or raw, interleaved, truncated), `answer_payload`
  (footnote citations), and `raise_for_failures` (fatal first, every-market failure, the list of
  partial failures to log). They raise `StaanMCPError` subclasses, which `server.py` turns into
  `ToolError` and `stdio_server.py` into an `isError` result; the text is the same.
- `sources.py`: `check_source_url` and `require_url_or_content`, raising `ValueError` (pydantic
  validators in the package, a plain `except` in the standalone server).

Written twice on purpose, because they cannot be shared: the HTTP transport (`httpx2` and `anyio`
against `urllib` and threads, with the same retry policy, redirect refusal and 32 MiB limit), the
argument checks (pydantic against hand-written checks with the same rules), and the protocol loop
(the MCP SDK against newline-delimited JSON-RPC). `stdio_server.py` puts its own parent directory on
`sys.path` only when it is not there yet, so it works run by path, installed and from inside the
bundle (where the archive is already on `sys.path`). Copied away from its repository there is
nothing to import: `_make_package_importable` then writes one line to stderr (the file must stay
in `src/staan_mcp/`, or use the installed package or the bundle) and exits with status 2, before
any traceback. A Python older than 3.11 gets a similar line (`staan-mcp needs Python 3.11 or
newer`) before anything else is imported.

- `ratelimit.py`: `RateLimiter`, the request rate limiter (4.11). It is imported by `client.py`
  and `stdio_server.py`, so the bundle derives it from the import graph like the other shared
  modules.

#### The bundle

`scripts/build_zipapp.py` builds `staan-mcp-stdio.pyz` with the stdlib `zipapp`. It is a script,
not package code: the installed package never contains it. It is inside the lint, type and
coverage gates (`pythonpath = ["scripts"]` and `--cov=build_zipapp` in `pyproject.toml`).

- The members are `stdio_server.py`, `__init__.py` and every module reachable from them, found
  by walking the imports with `ast` (no hand-kept list). An import of anything that is neither
  standard library nor a file of the package, or a relative import, stops the build.
  `__main__.py` is written by the script (`sys.exit(main())`): the one `zipapp` generates for
  `main=` drops the exit status, which would turn the usage error status 2 into 0.
- Reproducible: members in sorted order, timestamp 1980-01-01, mode 0644, `create_system` Unix
  (the default depends on the host), no compression (zlib output differs between builds of zlib),
  CRLF normalised to LF, and `zipapp` only prepends `#!/usr/bin/env python3`. The same sources
  give the same bytes on Windows and Linux; the release build builds twice and compares.
- `tests/repo/test_build_zipapp.py` tests the walker (every import form, rejected imports), the
  archive layout and reproducibility. `tests/repo/test_stdio_bundle.py` runs the real archive with
  `python -I -S` from another directory: handshake, `tools/list`, a tool call, the exit status,
  and that the archive holds exactly the `staan_mcp` modules a run imports (so a new import of
  `stdio_server` that is missing from the archive fails the test).
- `release.yml` builds it in the unprivileged `build` job into `dist-bundle/` (not `dist/`: PyPI
  accepts only wheels and sdists), smoke-tests it, uploads it with the `dist` artifact, attests it
  next to the wheel and sdist in the `attest` job (the SBOM stays bound to the wheel and sdist: it
  lists runtime dependencies, which the bundle has none of), and attaches it to the GitHub
  release.

Rules, enforced by tests:

- Nothing reachable from `stdio_server.py` imports `mcp`, `httpx2`, `anyio`, `pydantic` or `pyjwt`
  (`tests/integration/test_stdio_standalone.py`: `python -I -S`, an import blocker, an import
  census and a static walk of the imports).
- A copy of `stdio_server.py` alone prints the one-line message and exits with status 2, with no
  traceback (`test_stdio_standalone.py` copies only that file and runs it on `python -I -S`).
- The two behave the same on the same inputs (`tests/integration/test_stdio_parity.py`, whose
  docstring lists every known difference and which pins each protocol one row by row). Known
  differences: the text of a rejected argument (pydantic's report against one line), configuration
  errors reported per tool call instead of refusing to start, the exception class named in a
  transport failure (`ConnectError` against `ConnectionRefusedError`), `User-Agent`
  (`staan-mcp-stdio/<version>`), a key error in one market (the package cancels the others and
  drops their requests in flight; the standalone server cannot take back a request a thread has
  sent, it only stops the markets not yet requested), and the JSON-RPC lines the SDK drops in
  silence or validates deeper (`_meta`, `cursor`, what is nested in `capabilities`), which the
  standalone server answers (`-32700`, `-32600`) or tolerates.
- `VERSION` in `stdio_server.py` is rewritten by `cz bump` (`version_files`) and compared with
  `pyproject.toml` and `server.json` by `tests/repo/test_stdio_version.py`. It cannot read the
  installed metadata: from a checkout there is none.
- `stdio_server.py` is inside every gate (ruff, mypy, pylint, xenon, 100% coverage). Only mutmut
  skips it (`do_not_mutate`).
- How `stdio_server.py` ends (`tests/unit/test_stdio_shutdown.py` in process,
  `tests/integration/test_stdio_process_exit.py` as a real process). Status 0: the client closed
  stdin and every running call has been answered. Status 1: the client vanished, meaning a reply
  could not be written (`BrokenPipeError`, `ConnectionResetError`, or a closed stdout). `Server._send`
  is the one place replies are written; on the first failure it stops all output, sets the stop
  event of every call in flight and the loop ends at once, abandoning those calls (their threads
  are daemons and nobody is waiting for them). Then `main()` points descriptor 1 at the null device
  (otherwise Python flushes the failed bytes again at exit: "Exception ignored", status 120) and
  closes the stderr log for good, because a daemon thread that holds the lock of `sys.stderr` while
  the interpreter shuts down aborts it (SIGABRT, "could not acquire lock ... at interpreter
  shutdown"; 6 and 11 of 200 hang-up runs on Linux 3.12 and 3.11). Nothing is printed in either case.
  Status 130: Ctrl-C (`KeyboardInterrupt`); `Server.close()` waits for a reply being written, so no
  thread is mid-write at exit, then stops the calls. Status 2: bad arguments, or a copy of the file
  alone. A tool worker leaves the registry of running calls only after its reply is written, so the
  snapshot `serve()` joins at the end of the input cannot miss a worker that is mid-reply.

### 4.11 Request rate limiting (`ratelimit.py`)

Staan allows 20 requests per second per key. One `web_search` over many markets is one request
per market (`*` is 12), simultaneous calls add up and every retry is a request, so without a
limiter two `market: "*"` calls would send 24 requests in an instant and be answered with 429s.

Decisions:

- **Delay, never refuse.** A request that does not fit waits for a slot; the caller sees a slower
  call, not an error. The one error is the deadline's: if the slot would come after the call
  deadline (`STAAN_DEADLINE`), the request fails at once with the existing deadline message, and
  nothing is sent or booked for it (`reserve(max_wait=...)` returns `None`).
- **Sliding-window log, not a token bucket.** A bucket that starts full and refills at the rate
  admits about 2N requests in the second after a burst; the log of the last N start times admits
  at most N in any window. `RateLimiter(rate, clock)` keeps a sorted list of booked starts under
  a lock; `reserve()` books `max(now, starts[-N] + window)` and returns a `Slot(start, delay)`
  (FIFO: slots are handed out in call order, so waiters are served first come first served).
  Starts older than the window are dropped on each booking.
- **Shape.** From a rate of 1 up, the capacity is `floor(rate)` per one-second window (2.5 means
  2); below 1 it is one start per `1 / rate` seconds. `0` is "off": every slot is immediate and
  nothing is remembered. The default is 20 (`DEFAULT_RATE_LIMIT` in `config.py`, next to the
  other defaults). A higher value is allowed for plans with a higher cap.
- **Safety margin.** The window is `RATE_LIMIT_WINDOW + RATE_LIMIT_SAFETY_MARGIN` (1.0 s + 50 ms,
  `config.py`). The limiter timestamps the start of a send, Staan the arrival; thread and task
  wake-ups, connection set-up and coarse timers (15.6 ms on Windows) move the two apart by a few
  milliseconds, and without a margin two requests a hair under a second apart on the wire could
  make 21 in a second as Staan counts it. 50 ms covers that for about 5 % of throughput (19 per
  second at the default). The unit tests check the booked schedule against the exact window. The
  end-to-end tests can only see arrival stamps taken by a thread on the receiving side, which on a
  loaded machine lag by up to ~150 ms measured, so they count in windows 0.2 s shorter
  (`SCHEDULER_TOLERANCE` in `tests/rate_limit_helpers.py`); that is a property of the measuring,
  not of the limiter.
- **Where it applies.** Immediately before each actual send, inside the retry loop, so a retry
  takes its own slot, and search and answer share one budget. The wait counts against the call
  deadline and not against `STAAN_TIMEOUT`: the package waits with `anyio.sleep` before the
  `httpx2` request starts, `stdio_server.py` waits on the call's stop event
  (`stop.wait(delay)`) and computes the socket timeout afterwards.
- **Cancellation.** A waiter that is cancelled (an `anyio` cancel scope, `notifications/cancelled`,
  a fatal error in a sibling market, shutdown) calls `release(slot)` if its start has not been
  reached, so a request that never happens does not delay the ones behind it. A slot at the end of
  the queue (a whole call cancelled) is free again at once; a gap in the middle of the queue is not
  filled by later waiters, which keep the slots they have (simple, and the window invariant holds
  either way). A slot whose start was reached is not given back.
- **Waking early.** A sleep or event wait may end slightly early, so both transports loop on
  `remaining(slot)` until the slot's start has been reached by the limiter's own clock.
- **Scope.** A limiter belongs to one server: the package makes one per `StaanClient` (so per
  `create_server`, and servers in one process do not share budgets, which keeps tests isolated),
  the standalone server one per `Server`. Several server processes sharing one API key do not
  know about each other; their budgets add up, and `STAAN_RATE_LIMIT` is how an operator splits
  them.
- **Logging.** In the package a delayed request is logged at DEBUG on `staan_mcp.client`. The
  standalone server has no logger and stderr is its only log channel, so it writes one
  `staan-stdio: rate limit: ... delayed ...` line to stderr for every delayed request, whatever
  the log level. Both lines give the endpoint, the delay and the rate. Requests within the
  budget log nothing, and nothing logged contains a secret.
- **Clock.** `time.monotonic` by default, injectable for tests. `reserve()` reads it once, under
  the lock.

Tests: `tests/unit/test_ratelimit.py` (fake clock: exact delays for bursts, fractional rates,
idle recovery, `max_wait`, release, a Hypothesis check of the window invariant over random
bookings, waits and releases, and 16 threads hammering `reserve()`),
`tests/unit/test_client_rate_limit.py` (the client's use of it), and the end-to-end tests
`tests/integration/test_rate_limit_package.py` and `test_rate_limit_stdio.py`, which read the
monotonic arrival times that `LocalStaan` records and assert that no one-second window holds
more than the limit while every request still succeeds.

## 5. Repository files
- `README.md` needs:
  - badges and the `<!-- mcp-name: io.github.MarcinWolnyEU/staan-mcp -->` marker
  - quickstart for uvx, the three ways to run the server (installed, download and run, the
    `staan-mcp-stdio.pyz` release asset), Claude Desktop / Claude Code / VS Code configuration,
    and Docker
  - a tool reference with output examples
  - a transports section and an auth / threat-model section
  - a development section
- Also: `CHANGELOG.md` (Keep a Changelog), `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md` (Contributor Covenant 3.0), `LICENSE` (MIT, "Marcin Wolny"),
  `.gitignore`, `.editorconfig`, `.pre-commit-config.yaml` (prek-compatible), `Dockerfile`,
  `.dockerignore` and `server.json` (MCP registry schema 2025-12-11).
- `.github/`:
  - workflows `ci.yml`, `release.yml`, `mutation.yml`, `security.yml` (CodeQL, zizmor,
    pip-audit, dependency-review, gitleaks over the full history with a pinned binary and
    checksum) and `scorecard.yml`
  - `ci.yml` is also a reusable workflow (`workflow_call`, input `release`), and `release.yml`
    runs it on the tagged commit before anything is built. Its `ci-ok` gate ("CI passed")
    needs every job but the advisory Codecov upload, including a `metadata` job that checks the
    pyproject and `server.json` versions agree, runs the pre-commit hooks on every file
    (`SKIP=mypy,pylint,xenon`, which the lint job runs) and checks the PR title with `cz check`.
    Jobs that sync a narrow environment (`uv sync --no-default-groups --group ...`) set
    `UV_NO_SYNC=1` so `uv run` does not reinstall the default `dev` group. Every job has
    `timeout-minutes`, every `setup-uv` step pins uv `0.12.21`, and twine,
    check-wheel-contents and pip-audit run from locked groups (`dist`, `audit`).
  - `release.yml` builds and checks in an unprivileged `build` job (`contents: read`; twine and
    check-wheel-contents come from the locked `dist` group; the tagged commit must be an ancestor of
    `origin/main`), and only the small `attest` job holds `id-token` and `attestations: write`:
    it downloads the finished files, runs no project or tool code, and signs both the build
    provenance and the CycloneDX SBOM (`actions/attest-sbom`). Publishing needs both. Tags are
    PEP 440 (`cz bump --prerelease rc` gives `v0.2.0rc0`): the `build` job outputs `prerelease`,
    the GitHub release gets `--prerelease`, the image tags come from `type=pep440` (a pre-release
    gets only its own tag, never `X.Y` or `latest`), and pre-releases skip the MCP Registry. The
    registry job installs `mcp-publisher` against a pinned SHA-256 and waits until PyPI serves the
    new version before publishing. Images are built for `linux/amd64` and `linux/arm64`.
  - Releases: `CHANGELOG.md` is hand-written (Keep a Changelog), so `[tool.commitizen]` sets
    `update_changelog_on_bump = false` and `cz bump` only bumps `pyproject.toml`, `uv.lock` and
    `server.json` and tags. CONTRIBUTING.md has the release steps and the list of pins
    Dependabot cannot see.
  - `.pre-commit-config.yaml` has `detect-private-key` and a pinned `gitleaks` hook
  - `dependabot.yml` (uv, github-actions, docker, pre-commit; Conventional Commit prefixes
    `build`/`ci` with scope). pre-commit.ci is not used: its autoupdate would drop the SHA pins.
  - `ISSUE_TEMPLATE/`, `pull_request_template.md` and `CODEOWNERS`
- Start from the templates in `docs/research/python-packaging-best-practices-templates/`, with
  actions pinned by SHA.
- `.gitignore` MUST cover `*key*.txt`, `.tmp/`, `mutants/`, `.venv/`, caches, `dist/` and
  `.coverage*`; it and `.dockerignore` also cover `*.pem`, `*.key` and `credentials*`.
- The Dockerfile copies `/app/.venv` without `--chown`, so the runtime user cannot modify the
  installed code (bytecode is precompiled, `PYTHONDONTWRITEBYTECODE` is set). Base images are
  `ghcr.io/astral-sh/uv:0.12.21` and `python:3.13-slim-trixie`, written as literal
  `name:tag@sha256:<digest>` (no `ARG` in `FROM`, which Dependabot cannot parse). The
  `HEALTHCHECK` reads `STAAN_MCP_PORT`.
- `tests/repo/` checks these packaging and CI rules (workflow structure, pins, Dockerfile,
  Dependabot, pre-commit and commitizen settings, version agreement) with PyYAML, so a broken
  release pipeline fails `pytest` locally instead of on a tag push. The README documents
  `docker run --read-only --cap-drop=ALL --security-opt no-new-privileges --tmpfs /tmp`.
