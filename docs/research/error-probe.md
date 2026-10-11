# Staan API error probe (2026-09-30)

Probed `https://api.staan.ai/v2` with key 1 (`api key.txt`), key 2 (`api key 2.txt`) and synthetic bad credentials. Scripts and raw responses (full headers plus body, keys redacted, none present; verified by grep) are in `.tmp/error-probe/` (`common.py`, `p1.py`..`p7.py`, `raw/*.json`). About 130 calls, almost all cheap 4xx.

## 0. What changed vs earlier notes, and what could not be tested

| Item | Result |
|---|---|
| **Disabled key** | **NOT TESTED: `disabled key.txt` is a 0-byte file** (mtime 10:37, unchanged through the whole session; it probably was not saved). All three disabled-key calls (GET search, POST search, POST answer) are pending. Fill the file and run `cd .tmp/error-probe && uv run --with httpx python -c "from common import *; ..."` (or ask for a re-run). The mapping in section 4 has a defensive fallback so the server behaves sanely whatever the disabled response turns out to be. |
| **Key 1 on `POST /answer`** | **Still `403 {"message":"Forbidden","statusCode":403}`** for every body (valid, `{}`, invalid enums, oversize query, wrong content-type). Retried at three points over about 5 minutes (also after a 90 s wait), so it is not a short propagation lag. Authorization runs before validation. So key 1 is NOT yet allowlisted for Answer, despite the expectation. Only malformed JSON gets past the gate (`400`, see 2.9). |
| **Key 2 on `POST /answer`** | `403 Forbidden`, as expected (identical to key 1, body and headers). |
| **`extra_snippets` with key 1** | **NOW WORKS** (was absent before). See section 3. Key 1 apparently has the "Web Search for AI" tier now; Answer is a separate gate that is still closed. |
| **`count` 1, 5, 20, 50** | Still rejected for both keys; only 10 accepted. See section 3. |

## 1. Common response properties (all errors)

- Content-Type `application/json; charset=utf-8` (except the bare 404 below). No `Retry-After`, no `RateLimit-*`/`X-RateLimit-*`, no `WWW-Authenticate` on any 401 (checked all).
- Headers on every response (200 and errors alike): `vary: Origin`, `access-control-allow-credentials: true`, a long `content-security-policy`, `cross-origin-*`, `x-*` security headers, `strict-transport-security`, `content-length`, `etag` (on errors), `date`, `x-envoy-upstream-service-time`, `server: istio-envoy`. `x-response-time` appears only on successful searches. Nothing useful for error handling besides status and body.
- Latency of auth and validation errors: about 0.5 s.
- Body shape families (parser must accept all):
  1. Nest style: `{"message": <str | list[str]>, "error": "<Reason>", "statusCode": <int>}` (400, 404).
  2. Short Nest style without `error`: `{"message": "Unauthorized"|"Forbidden", "statusCode": <int>}` (401 no-header, 403).
  3. Gateway style: `{"code": "INVALID_CREDENTIALS", "message": "Unknown API key or invalid secret"}` (401 bad key; no `statusCode`, no `error`).
  4. Empty body (404 for unknown top-level route).

## 2. Catalogue

All bodies exact (whitespace normalised). `Bearer <K>` = a real key, `<X>` = random key.

### 2.1 Missing Authorization header (all three endpoints)

| request | status | body |
|---|---|---|
| GET `/v2/search/web?q=..&market=en-gb`, no header | 401 | `{"message":"Unauthorized","statusCode":401}` (43 bytes) |
| POST `/v2/search/web`, no header | 401 | same |
| POST `/v2/answer`, no header | 401 | same |

### 2.2 Malformed Authorization header (GET `/v2/search/web`; answer confirmed for the no-prefix case)

| header value | status | body |
|---|---|---|
| `<K>` (raw key, no scheme) | 401 | `{"code":"INVALID_CREDENTIALS","message":"Unknown API key or invalid secret"}` (same on POST `/answer`) |
| `Basic <K>` | 401 | INVALID_CREDENTIALS |
| `Token <K>` | 401 | INVALID_CREDENTIALS |
| `Bearer <K> extra` | 401 | INVALID_CREDENTIALS |
| `Bearer "<K>"` (quoted) | 401 | INVALID_CREDENTIALS |
| `Bearer` (scheme only, no token) | 401 | `{"message":"Unauthorized","statusCode":401}` (same as missing header) |
| `Bearer ` (trailing space, empty token) | not sendable | httpx refuses (`Illegal header value`); a client library never puts this on the wire |
| `bearer <K>` (lowercase scheme) | **200** | accepted, scheme is case-insensitive |
| `Bearer  <K>` (two spaces) | **200** | accepted, whitespace tolerant |

Rule: a raw key with no scheme is treated as an unknown credential (INVALID_CREDENTIALS), not as "missing". Note the raw-key case sends the real key in a wrong header, so any proxy log would show it; the MCP server should always build the header itself and strip whitespace/newlines from the env value (a trailing newline in `STAAN_API_KEY` would raise `LocalProtocolError` client-side).

### 2.3 Wrong keys

| token | status | body |
|---|---|---|
| random 47 chars (same length as key 1), random 36 chars, UUID-shaped, last char of key 1 or key 2 flipped, first half of key 1 | 401 | `{"code":"INVALID_CREDENTIALS","message":"Unknown API key or invalid secret"}` |
| same random key on POST `/search/web`, POST `/answer` (valid body, and `{}`) | 401 | INVALID_CREDENTIALS (auth precedes validation) |
| random token of length <= 9 (tested 1, 8, 9; 6-char `abc123`) | 401 | `{"message":"Unauthorized","statusCode":401}` |
| random token of length >= 10 (tested 10, 11, 12, 16..47) | 401 | INVALID_CREDENTIALS |

So the gateway distinguishes by token length: tokens shorter than 10 characters get the plain `Unauthorized` shape. Both are 401 and both mean "bad or missing credential"; the code must not depend on the difference. Key shapes seen (not values): key 1 is `xxx_<23>_<19>` (prefix, id, secret, matching the "key or secret" wording), key 2 is a 36-char UUID.

### 2.4 Key 2 (search only) on Answer

| request | status | body |
|---|---|---|
| POST `/v2/answer` valid body | 403 | `{"message":"Forbidden","statusCode":403}` (40 bytes) |
| POST `/v2/answer` `{}` | 403 | same (authz before validation) |
| POST `/v2/answer` malformed JSON | 400 | see 2.9 (body parser runs before authz) |
| GET `/v2/answer?query=x` | 404 | `{"message":"Cannot GET /v2/answer?query=x","error":"Not Found","statusCode":404}` |

Key 2 on GET `/v2/search/web`: 200, normal (control).

### 2.5 Key 1 on Answer (expected 200, actual 403)

Same as 2.4 for every body tried (valid English query with `markdown:true, stream:false`; `{}`; `language:"de"`; `mode:"medium"`; empty query; 2001-char query; non-string query; URL in `filter`; bad `history`; form-encoded body). Answer success shape and Answer 400 validation messages therefore remain **unverified**. Re-run `.tmp/error-probe/p3.py` once support enables the key (it already covers those bodies).

### 2.6 Disabled key

Not tested (empty key file, see section 0). Fill `disabled key.txt` and run GET `/search/web`, POST `/search/web`, POST `/answer` with it.

### 2.7 Validation errors on search (400; class-validator, `message` is a list)

- Missing/empty `q`: `["q must be longer than or equal to 1 characters"]` (GET without `q`, POST with empty body).
- Several failures are reported together and in one array, e.g. GET `q=&market=xx&count=3&offset=7&full_content=text` gives `["full_content must be one of the following values: markdown, html","q must be longer than or equal to 1 characters","market must be one of the following values: fr-fr, de-de, en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz, en-za, en-sg","count must be equal to 10","offset must be a factor of 10"]`.
- POST with a JSON array body (`[1,2]`) yields 9 messages (min_score range, q, market, count, offset checks); no special "body must be object" message.
- Other messages (earlier notes, unchanged): `count must be equal to 10`; `offset must not be greater than 30`; `offset must not be less than 0`; `offset must be a factor of 10`; `max_snippets must not be less than 1` / `greater than 10`; `min_score must not be greater than 1`; `include_domains contains an invalid domain`; `include_domains must contain no more than 10 elements`; `include_domains cannot be used with exclude_domains`.
- Content-Type is lenient: POST `/search/web` with `application/x-www-form-urlencoded` body `q=hello&market=en-gb` returns 201 (form body parsed). Not relevant to us.

### 2.8 Routing errors

| request | status | body |
|---|---|---|
| GET `/v2/answer` (with or without auth) | 404 | `{"message":"Cannot GET /v2/answer","error":"Not Found","statusCode":404}` |
| PUT / DELETE `/v2/search/web` | 404 | `{"message":"Cannot PUT /v2/search/web?q=x","error":"Not Found","statusCode":404}` (DELETE analog) |
| GET `/v2/nope` (unknown top-level path), with or without auth | 404 | **empty body**, no content-type |
| HEAD `/v2/search/web?q=x` | 200 | empty body (may be billed as a search; never use) |

Unknown routes are 404 before authentication (no 401 for missing key).

### 2.9 Malformed JSON body (both endpoints, before auth on answer)

`POST` with body `{not json` on `/v2/search/web`, `/v2/answer` (key 1 and key 2): `400 {"message":"Expected property name or '}' in JSON at position 1 (line 1 column 2)","error":"Bad Request","statusCode":400}`. Here `message` is a **string** (a Node `JSON.parse` message), not a list. An MCP client built on httpx `json=` cannot produce this, so it only matters as a parser edge case.

## 3. Re-verification results

### `count` (key 1 and key 2, GET and POST, en-gb, query `open source llms`)

| count | key 1 GET | key 1 POST | key 2 GET | key 2 POST |
|---|---|---|---|---|
| 0, 1, 5, 9, 11, 20, 50 (GET); 1, 5, 20, 50 (POST) | 400 | 400 | 400 | 400 |
| 10 | 200 | (201 earlier) | 200 | (201 earlier) |

Every rejection is exactly `{"message":["count must be equal to 10"],"error":"Bad Request","statusCode":400}`, also combined with `full_content=markdown`. Nothing else works, so **min = max = 10; no bisection possible or needed**. Client-side paging emulation (offset 0/10/20/30, dedupe by URL, slice) stays mandatory; the upstream `count` must always be 10 or omitted.

### `extra_snippets` (key 1): works now

Tested GET and POST, `en-gb`, `en-us`, `fr-fr`, `max_snippets` 3/5/10, `min_score` 0/0.2/default, with and without `full_content=markdown`: 7 of 7 calls returned `extra_snippets`. A control call without the parameter has none.

- Every result carries the key when enrichment ran: a list of `{"chunk": str, "score": float}` objects (never plain strings), sorted by score descending. Results whose page could not be fetched carry `[]` (about 1-3 of 10 per query). So the key is present even when empty, but keep tolerating absence.
- `max_snippets` is honoured as an upper bound (3, 5, 10 tested; fewer when few chunks pass). `min_score` is honoured (0 gave scores down to 0.32; default 0.1 gave >= 0.58 in one sample; the default is effectively lenient since observed scores are high).
- Chunk length 312..1557 chars, contain markdown fragments (backticks, `**bold**`, `>` quotes, newlines). Score range observed 0.32..0.998.
- With `extra_snippets=true`, `published_date` appears on about 5-7 of 10 results even without `full_content`.
- `extra_snippets` and `full_content` combine fine (`full_content` lengths 1,425..45,399; 0 for unfetchable pages).
- Latency with extra_snippets: 1.1-2.95 s (plain about 0.9-1.5 s).
- Response top-level keys unchanged (`search_id`, `query`, `web`); no `confidence`.

Consequence: the search `simple_response` join `snippet (...) chunk1 (...) chunk2` will now be populated for key 1 and must use `e["chunk"]`. Chunks are long (up to about 1.5k chars) and up to `max_snippets` per result, so a per-result 3-snippet default yields up to about 4.5k chars extra per result (10 results, about 45k chars); consider whether to cap. Add a live smoke test and keep mock tests for `[]`, absent key, and plain-string chunks.

Key 2 re-tested (GET, `extra_snippets=true&max_snippets=3`, en-gb): 200, 10 results, **no `extra_snippets` key** on any result. So the enrichment tier is key-specific: key 1 has it, key 2 does not (and the parameter is silently ignored, not an error).

## 4. Recommended mapping: upstream error to `ToolError` message

Parse rule (one function): read `status`; body JSON if parseable else text (truncate to 300 chars); `message = body.get("message")`, if list join with `"; "`; `code = body.get("code")`. Never include the API key or the Authorization header in any message or log. Retry only 429, 502, 503, 504 and transport errors; never 400/401/403/404.

| upstream | detection | user-facing `ToolError` text |
|---|---|---|
| no key configured locally | env `STAAN_API_KEY` empty or unset (check before any request, do not send) | `STAAN_API_KEY is not set. Create a key in the Staan console and set it in the MCP server environment.` |
| 401, `code == "INVALID_CREDENTIALS"` | body code | `Staan rejected the API key (unknown key or invalid secret). Check STAAN_API_KEY: it must be the full key with no quotes or extra characters.` |
| 401, `{"message":"Unauthorized"}` | no code | `Staan did not accept the credentials (401 Unauthorized): the key is missing, empty or malformed. Check STAAN_API_KEY.` |
| 401 (either shape) | fallback | Same family; one message is enough: `Staan authentication failed (401). Check that STAAN_API_KEY is set to a valid, enabled key.` |
| 403 on `/answer` | path and status | `The Answer API is not enabled for this API key. Answer is invitation-only; ask support@staan.ai to allowlist the key. Web search is not affected.` |
| 403 on `/search/web` (not observed; expected for a disabled key, unverified) | path and status | `Staan refused this request (403 Forbidden). The API key may be disabled or lacks access to web search. Check the key status in the Staan console.` |
| Disabled key (status and body unknown, section 2.6) | do not special-case until tested; if the body message mentions `disabled`/`inactive`/`revoked`/`suspended` (case-insensitive) | `This Staan API key is disabled. Enable it or use another key (Staan console).` |
| 400 validation, `message` list | list of strings | `Staan rejected the request: <msgs joined with "; ">.` The server validates inputs first, so this should be rare; the case that can still happen is Answer (unverified messages). |
| 400 `count must be equal to 10` | substring | Internal bug (the server must always send 10); message `Internal error: unsupported count sent upstream. Please report this.` |
| 400 `market must be one of...` | substring | Client-side validation should catch it earlier; if seen: `Unsupported market. Valid markets: <list from the upstream message>.` |
| 400, `message` is a string (malformed JSON) | str | `Staan could not parse the request body: <message>.` (internal bug) |
| 404 | any | `Staan endpoint not found (404) for <METHOD> <path>. The server may be outdated; please upgrade staan-mcp.` (body may be empty) |
| 429 | status | Retry with backoff (honour `Retry-After` if it ever appears; none observed). After the retry budget: `Staan rate limit reached (20 requests/s for search). Try again shortly or lower the number of markets or count.` |
| 500 / 502 / 503 / 504 | status | Retry with backoff. After the budget: `Staan is temporarily unavailable (<status>). Try again later.` |
| Timeout | `httpx.TimeoutException` | `Staan did not answer within <n> s (search with full_content or extra_snippets can be slow). Retry or disable full_content.` |
| Connect/DNS/TLS error | `httpx.TransportError` | `Could not reach api.staan.ai: <error class>. Check the network connection.` |
| Any other status | fallback | `Staan returned HTTP <status>: <message or first 200 chars of body>.` |
| Non-JSON or empty 2xx body | parse failure | `Staan returned an unreadable response (HTTP <status>).` |

Multi-market fan-out: if some markets fail with 4xx auth errors, all will (same key), so short-circuit on the first 401/403 and raise once, instead of aggregating N identical errors. For other errors follow gaps.md C3 (partial success returns the successful results).

## 5. Facts to carry into implementation

1. The two 401 body shapes and the 400 `message` str-or-list variance are real: parse defensively.
2. Bearer scheme is case-insensitive and whitespace tolerant upstream; always emit `Authorization: Bearer <key>` with a stripped key.
3. Unknown top-level routes give an empty 404 body; do not assume JSON.
4. Auth precedes validation for search and answer, except malformed JSON (400 before auth on POST); 403 hides Answer validation errors until the key is allowlisted.
5. Key 1 can use `extra_snippets` now; Answer is still gated for both keys. Answer live verification (citations, response shape, 400 messages) remains blocked; re-run `p3.py` after allowlisting.
6. Still open: disabled key response; 429 behaviour.

## Disabled key (verified 2026-09-30, after `disabled key.txt` was filled)
- GET /v2/search/web and POST /v2/answer with a disabled key both return
  `403 {"code":"KEY_DISABLED","message":"API key is disabled"}`.
- A 403 that is NOT allowlisted for Answer returns `403 {"message":"Forbidden","statusCode":403}`
  (no `code`), so the two cases can be told apart by `code == "KEY_DISABLED"`.
- Key 1 (`api key.txt`) was still refused on /v2/answer (403 Forbidden, even for `{}`) after the
  user reported account approval. Search with the same key returns 200.
