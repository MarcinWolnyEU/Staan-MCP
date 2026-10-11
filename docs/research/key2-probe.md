# Key 2 probe findings

## Answer

Probed 2026-09-30 with the second key ("api key 2.txt"). Scratch and raw (redacted) responses: `.tmp/key2-answer/` (`common.py`, `p1.py`, `p2.py`, `raw/t01..t03*.json`).

**Result: key 2 is also NOT allowlisted for `POST https://api.staan.ai/v2/answer`. Every request returns `403 {"message":"Forbidden","statusCode":403}`. Probing stopped after 4 calls, as instructed.**

| call | body | status | response |
|---|---|---|---|
| t01 | `{"query":"What is the capital of Australia?","language":"en","stream":false,"markdown":true}` | 403 | `{"message":"Forbidden","statusCode":403}` |
| t02 | `{}` (invalid body) | 403 | same, so authorization runs before validation (as with key 1) |
| t03 | `sources` + `sources_mode=context` body | 403 | same |
| control | wrong bearer token | 401 | `{"message":"Unauthorized","statusCode":401}` |

Interpretation: the key authenticates (a bad key gives 401, not 403); the 403 is an allowlist/plan gate on the Answer route, identical to key 1. Two independent keys both being refused suggests the gate is account-level ("Answer API is currently available by invitation only", contact support@staan.ai), not a per-key quirk.

Consequently none of the following could be verified live, and the assumptions in `gaps.md` section B5 / D1 remain the working hypotheses (unchanged, not confirmed and not contradicted): citations structure, `position` semantics (code points vs UTF-16, sentence/word boundary), marker-free answer text, `source_id` base and mapping, multiple/repeated citations, `markdown` true vs false differences, `related_queries`/`usages`/`request_id` live presence, the 400/oversize/invalid-language error bodies, and the allowed `language` values (docs/OpenAPI: `fr`, `en` only), and which `filter` forms the API accepts (search `include_domains` accepts bare domains only; normalise the Answer `filter` the same way).

### What the implementation should do given this

1. Keep the defensive `to_gfm(answer, citations, sources)` from `gaps.md` B5 (position = insertion index into `answer`, key strictly on `sources[].id == source_id`, number by first appearance, dedupe per source, definitions `[^n]: url` for cited sources only, no-op when citations are empty), isolated behind one helper so the offset unit can be changed in one place.
2. Test it only with mocked responses (respx / `httpx.MockTransport`), including: empty citations, unknown `source_id`, repeated source, several citations at one position, out-of-range position, astral characters in the answer, and answer text that already contains `[n]`.
3. Map 403 on `/answer` to a clear ToolError ("this API key is not allowlisted for the Answer API; contact support@staan.ai"); do not retry on 403.
4. Live verification is blocked until Staan allowlists an account. Once it does, re-run one call with `markdown=true` (plus one with non-ASCII text, e.g. French with accents and an emoji in the query) and check the items listed above.

## Search

Probed 2026-09-30 with key 2 on `GET` and `POST https://api.staan.ai/v2/search/web`. Scratch and raw (key-free) responses: `.tmp/key2-search/` (`probe.py`, `cmp.py`, `raw/*.json`). About 60 calls. Key 1 was used for one side-by-side comparison call only.

**Result: key 2 behaves identically to key 1 on search. No plan-tier difference found.**

### 1. `count`

Tested 0, 1, 2, 5, 9, 10, 11, 20, 25, 49, 50, 51 on both GET and POST (market `en-gb`).

- Only `count=10` works (GET 200, POST 201, 10 results).
- Every other value, GET and POST, returns `400 {"message":["count must be equal to 10"],"error":"Bad Request","statusCode":400}`.
- Bisecting is moot: min = max = 10. Client-side paging emulation of `count` (offset 0/10/20/30, dedupe by URL, slice) is still required, as in `gaps.md` B1.

### 2. `offset`

Query `best pizza in london`, `en-gb`.

- 0, 10, 20, 30 return 200 (GET) with 10 results each; POST offset 30 returns 201.
- 40 (GET and POST): `400 ["offset must not be greater than 30"]`.
- 5: `400 ["offset must be a factor of 10"]`.
- -1: `400 ["offset must be a factor of 10","offset must not be less than 0"]`.
- Ceiling is 4 pages, at most 40 raw results per query and market. Same as key 1.

### 3. `extra_snippets`

Eight variants (GET string `"true"` and POST boolean `true`; `en-us`, `en-gb`, `fr-fr`; `max_snippets` 3, 5, 10; `min_score` 0 and 0.2; with and without `full_content=markdown`; plus a key 1 vs key 2 side-by-side call with `extra_snippets=true&max_snippets=5&min_score=0&full_content=markdown`).

- All return 200/201, but **no result carries an `extra_snippets` key at all** (not even `[]`). Same for key 1 today. Feature still not observable, so its shape (`[{chunk, score}]` per docs) remains unverified live. Treat as optional and test with mocks.
- Result keys without enrichment: `display_url, favicon_url, hostname, snippet, title, url`. With `full_content`: plus `full_content {text, format, length}` and `published_date` (present on some results only, e.g. 4-5 of 10).
- Top-level keys: `search_id`, `query`, `web` only. `search_id` is 32 hex chars (not a dashed UUID). `confidence` is absent (both keys). `query.altered_query` not seen. `query.count` echoes 10.
- A page can be short: `open source llms` en-us returned 9 results on both GET and POST.

### 4. Differences vs key 1

None observed: same status codes, validation messages, response keys, header set (`x-response-time`, `x-envoy-upstream-service-time`, no rate-limit headers), and same `full_content` shape. Key 2 does not unlock `extra_snippets` or `confidence`, and (see `## Answer`) does not unlock Answer either, so both keys appear to be on the same plan tier. Only the account/plan owner can confirm whether "Web Search for AI" (extra snippets) needs a plan change.
