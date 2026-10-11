# Staan API: live probing findings

Probed 2026-09-30 against `https://api.staan.ai/v2` with the project key (Bearer). Scripts and raw responses (key redacted, none present) live in `.tmp/staan-api-live/` (`probe.py`, `p*.py`, `a*.py`, `raw/*.json`). About 110 HTTP calls were made (most were cheap 400s).

Sources: docs pages `https://docs.staan.ai/api/search` and `/api/answer` (Zudoku SPA; the OpenAPI schema is embedded as JSON in the HTML, which is the authoritative spec), plus `/docs/web-search`, `/docs/web-for-ai`, `/docs/answer-v2`. The prose docs contradict the OpenAPI schema and the live API in places; **live behaviour wins**.

## 1. Endpoints

| Endpoint | Method | Success status | Notes |
|---|---|---|---|
| `/v2/search/web` | GET | 200 | query-string params |
| `/v2/search/web` | POST | **201** | JSON body, adds `include_domains` / `exclude_domains` |
| `/v2/answer` | POST | 200 (documented) | **403 Forbidden for our key** (see section 8) |

Auth: `Authorization: Bearer <key>`. Unknown JSON body fields / unknown query params are silently ignored (no 400). `GET /v2/answer` is 404 (`Cannot GET /v2/answer?query=x`). `/v2/answers` is 404.

## 2. `count` (the big surprise)

**Only `count=10` is accepted, on both GET and POST**, with or without `extra_snippets` / `full_content`.
Every other value (0, 1, 2, 20, 50, 51, 100, "abc", "10.5") returns:

```
400 {"message": ["count must be equal to 10"], "error": "Bad Request", "statusCode": 400}
```

So bisecting is moot: min = max = 10. Omitting `count` gives 10. Docs (`/docs/web-search`) say "Fixed at 10"; the OpenAPI schema just says "number, defaults to 10" (misleading).

Pagination via `offset`:
- Valid: 0, 10, 20, 30. Others: `offset=40` gives `400 ["offset must not be greater than 30"]`; `offset=5` gives `["offset must be a factor of 10"]`; `offset=-1` gives both `["offset must be a factor of 10", "offset must not be less than 0"]`.
- Hard ceiling = 40 results per query (4 pages).
- Across the 4 pages of one query I got 40 results, 38 unique URLs, so **dedupe by URL when stitching pages**.
- A page can return fewer than 10 results (9 seen for en-gb/en-us "weather forecast", and for a 401-char query), so a page is not always full.

**Implication for the MCP `count` (1-50):** the API cannot honour it natively. Implement client-side: `pages = ceil(count/10)` requests with `offset = 0,10,20,30` (concurrent), concatenate in order, dedupe by `url`, slice to `count`. Effective maximum is 40 (50 requested would return at most ~38-40). Either clamp the MCP schema to 1-40 or accept up to 50 and document that results are capped at 40. This multiplies cost: count=50 costs 4 API requests per market. Multi-market wildcard queries multiply again.

## 3. `market`

- Default when omitted: `fr-fr` (echoed in `query.market`). Docs say default fr-FR. The MCP must send `en-gb` explicitly.
- Valid set, exactly 12 (the OpenAPI enum, confirmed by the 400 message): `fr-fr, de-de, en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz, en-za, en-sg`. All 12 returned 200 with market-appropriate results.
- Input is case-insensitive: `EN-GB` is accepted and echoed lowercase `en-gb`.
- Rejected (400): `fr-ca`, `fr-be`, `en` (bare language), `en_GB` (underscore), `xx-yy`, `es-es`, `de-at`, empty string.
- Invalid-market error, verbatim:
  ```
  400 {"message": ["market must be one of the following values: fr-fr, de-de, en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz, en-za, en-sg"], "error": "Bad Request", "statusCode": 400}
  ```
- Wildcard expansion (`en-*`, `en*`, `*-fr`) is therefore best done against the hard-coded 12-entry list: `en-*` = en-fr,en-us,en-gb,en-ca,en-au,en-in,en-ie,en-nz,en-za,en-sg (10 markets); `*-fr` = fr-fr, en-fr; `*-*` = all 12. A pattern matching nothing (e.g. `fr-ca`, `es-*`) should be a clear client-side error. Expanding `en-*` costs 10 x pages requests, so consider a cap on total fan-out.

## 4. `full_content`

- Accepted: `markdown`, `html` only. Rejected (400): `text`, `none`, `MARKDOWN` (case-sensitive here, unlike market), `true`, empty string:
  `400 {"message": ["full_content must be one of the following values: markdown, html"], ...}`.
  To get "no full content" omit the parameter.
- Shape: each result gets `full_content: {"text": str, "format": "markdown"|"html", "length": int}`. `length` equals `len(text)` (was off by a few characters for html, e.g. 171109 vs 171104, likely bytes vs chars or trimming, so do not rely on it).
- **The object is present on every result but `text` can be empty** (`length: 0`, e.g. youtube.com, some blocked pages) and can be a junk stub (81 chars: "This store requires javascript..."). The MCP should omit `full_content` for empty text (the requested output says `full_content (optional)`).
- markdown size: 81 to ~30,000 chars per page typical (up to ~80k for Wikipedia); html: 45k-350k chars per page, so markdown is the sane default.
- `published_date` (ISO 8601 with `Z`, e.g. `2024-03-22T00:00:00Z`) appears on some results only when `full_content` is set (and null/absent otherwise).
- Result order can differ between calls (markdown vs html runs of the same query ordered results slightly differently).

## 5. Search response shape (GET 200 / POST 201, identical)

```json
{
  "search_id": "25c170a11e5507aef42c02a743592763",
  "query": {"q": "...", "market": "en-gb", "count": 10, "offset": 0},
  "web": {"results": [
    {"title": "...", "url": "https://...", "snippet": "... (often ends with '...')",
     "display_url": "https://...", "hostname": "www.example.com",
     "favicon_url": "https://s.qwant.com/v1/fav/...",
     "full_content": {"text": "...", "format": "markdown", "length": 5118},
     "published_date": "2024-03-22T00:00:00Z"}
  ]}
}
```

- Always present per result: `title, url, snippet, display_url, hostname, favicon_url` (favicon was present in all my probes; schema says optional). `thumbnail` (optional) never observed.
- Top-level keys observed: `search_id` (32 hex chars, not a dashed UUID), `query`, `web`. `confidence` (schema: `{score, dispatcher{decision, version, threshold_validation}}`) was **never returned** in any probe. `query.altered_query` never observed.
- Query strings longer than 400 chars are **silently truncated to 400** (echoed `query.q` has length 400), not rejected. Empty/missing `q` gives `400 ["q must be longer than or equal to 1 characters"]`.
- `site:domain` in `q` works (only realpython.com results, echoed verbatim in `query.q`).

## 6. `extra_snippets` (important for the `simple_response` "snippet (...) extras" join)

- Documented shape: `extra_snippets: [{"chunk": str, "score": float 0-1}]` (objects, **not strings**), controlled by `max_snippets` (1-10, default 3; 0 gives `400 ["max_snippets must not be less than 1"]`, 11 gives `["max_snippets must not be greater than 10"]`) and `min_score` (0-1, default 0.10; out of range gives 400 "must not be less than 0" / "must not be greater than 1").
- **Live: `extra_snippets` was never returned**, in 8+ attempts (GET and POST, en-gb/en-us/fr-fr, min_score 0.01/0.2/0, max_snippets 1-10, with and without `full_content`). The param is accepted (200/201) but results carry no `extra_snippets` key. Possibly gated by plan or a currently disabled feature. The MCP must therefore treat `extra_snippets` as optional and handle absence; the join logic should use `[r["snippet"]] + [e["chunk"] for e in r.get("extra_snippets", [])]` joined with `" (...) "`, and tolerate chunks given as plain strings defensively. I recommend a mock-based test for the populated shape since it cannot be reproduced live.
- Docs: extra_snippets fetches pages, has per-URL fetch timeout 3.5 s and global timeout 4 s; recommended client timeout 8-10 s with enrichment.

## 7. Domain filters (POST search)

- `include_domains` / `exclude_domains`: string[], max 10 (11 gives `400 ["include_domains must contain no more than 10 elements"]`, despite the OpenAPI text saying extras are dropped). Both together: `400 ["include_domains cannot be used with exclude_domains"]`.
- **Values must be bare domains**: `"https://www.realpython.com/"` gives `400 ["include_domains contains an invalid domain"]`; `"realpython.com"` / `"docs.python.org"` work and filter correctly. Same normalisation (strip scheme, path, probably `www.`) is therefore also prudent for the Answer `filter`.

## 8. Answer endpoint: NOT probe-able with this key

Every request to `POST /v2/answer` with a valid key returns, regardless of body (even `{}` or invalid enums, so authorization runs before validation):

```
403 {"message": "Forbidden", "statusCode": 403}
```

Docs (`/docs/answer-v2`): "Answer API requires invitation; contact support@staan.ai for access." Bad key gives the normal 401. So none of these could be verified live; the following is from the docs/OpenAPI only:

- Request: `query` (string, required, 1-2000 chars), `filter` (string, "canonical, no www/scheme", e.g. `lemonde.fr`; a single string, not an array), `stream` (bool, false), `history` (max 20 items strictly alternating user/assistant; user max 2000 chars, assistant max 10000), `mode` (`short`|`long`, default `short`), `language` (`fr`|`en`, **default `fr`**, so the MCP must default/send `en` deliberately), `markdown` (bool, default false; MCP wants true), `related_queries` (bool, false, up to 4-5), `query_rewrite` (bool, true), `sources` (max 10 of `{url?, title?, content? (max 50000)}`), `sources_mode` (`search`|`context`).
- Response 200: `{"request_id": str, "answer": str, "citations": [{"position": number, "source_id": str}], "related_queries": [str], "sources": [{"id": str, "url": uri, "title": str}], "usages": [{"step": "answer"|"query_rewrite"|"related", "input_tokens": n, "output_tokens": n}]}`.
- **Citation marking inside `answer` is undocumented and unverified.** Docs only say `citations` is an array of `{position, source_id}`; the `position` is presumably a character offset into `answer` (streaming events order: sources, assistant, citation, related, done, usages). To build the requested GFM footnotes (`[^1]` inline plus `[^1]: url` list) the implementation must map `citations[i].position` to an insertion point in `answer` and `source_id` to `sources[].id` to get the url. **Whether `position` is a character offset, a 1-based marker index, or something else must be confirmed once access is granted**; until then, implement defensively (if `answer` already contains `[n]`-style markers, convert them; else insert by offset; clamp out-of-range positions; dedupe footnotes per source) and unit-test both interpretations behind a single small function.
- Error codes documented: 401, 500.
- **Action item for the user:** request Answer API access from support@staan.ai (or verify the key's plan); until then the `answer` tool will return 403.

## 9. Error formats

| Case | Status | Body |
|---|---|---|
| Invalid key (present but wrong) | 401 | `{"code": "INVALID_CREDENTIALS", "message": "Unknown API key or invalid secret"}` |
| No Authorization header | 401 | `{"message": "Unauthorized", "statusCode": 401}` |
| Validation error | 400 | `{"message": ["<msg1>", "<msg2>"], "error": "Bad Request", "statusCode": 400}` (`message` is a **list** of strings, NestJS class-validator style) |
| Unknown route / wrong method | 404 | `{"message": "Cannot GET /v2/search/nope?q=x", "error": "Not Found", "statusCode": 404}` |
| Answer not enabled for key | 403 | `{"message": "Forbidden", "statusCode": 403}` |
| Rate limit (429) | not triggered | see below |

The two 401 shapes differ (gateway vs. app). A robust error mapper should read `message` as str or list[str], and optional `code`.

Rate limits: docs say 20 req/s (search). **No rate-limit headers** (`x-ratelimit-*`, `retry-after`) appeared on any response (200, 400, 401). A burst of 40 concurrent requests (invalid-market 400s, so cheap) produced no 429; I did not burst valid billable searches. The MCP should cap concurrency (e.g. semaphore of about 8-10 in flight, given the page x market fan-out) and treat 429 / 5xx with exponential backoff honouring `Retry-After` if it ever appears. Free tier: 1,000 requests/month, so a fan-out of 10 markets x 4 pages = 40 requests is 4% of the monthly free quota per call; worth a documented guard (max fan-out).

## 10. Latency

Measured from the client (Windows, EU/PL): plain search median 1.57 s (min 0.58, max 2.70; n=36). With `full_content` about 1.9-3.3 s (n=3). 400/401 validation errors about 0.5 s. `x-response-time` header example 317 ms server side (`x-envoy-upstream-service-time`); the rest is network. Suggested client timeout: 15 s search plain, 30 s with `full_content`/enrichment (docs suggest 8-10 s minimum).

## 11. Summary of MCP design consequences

1. `count` 1-50 must be emulated by paging (`offset` 0/10/20/30); hard cap 40; dedupe by URL; expect short pages.
2. Send `market` explicitly (default `en-gb`); the valid set is the 12 listed markets; wildcard expansion against that list; case-insensitive; client-side error for zero matches.
3. `full_content` accepts `markdown|html`; default to `markdown`; drop empty `text`.
4. `min_score` and `max_snippets` untouched (server defaults); do not expose `min_score`.
5. `extra_snippets` needs to be requested for the snippet-join feature but is not populated live; tests must use fixtures; the join must be optional.
6. Search GET returns 200, POST returns 201: accept both 2xx.
7. Answer endpoint is gated (403) for the current key; response shape from OpenAPI only; citation position semantics unverified.
8. Strip scheme/www/path from any domain filter (API rejects URLs in `include_domains`).
