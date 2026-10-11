# Staan API: documentation + live probing notes

Researched 2026-09-30. Sources: docs.staan.ai (introduction, docs/web-search, docs/web-for-ai, docs/answer-v2, api/answer, api/search), the OpenAPI schema embedded in the docs site bundle (saved at `.tmp/staan-api-docs/openapi.json`, extracted from `https://docs.staan.ai/assets/api-openapi.yaml-BKcS3Gzh.js`), https://staan.ai, https://github.com/Qwant/answer-node (TS SDK), and live calls with the project key (`api key.txt`, never recorded here).

No public `openapi.json`, `llms.txt`, `llms-full.txt` (all 404). Sitemap lists only: `/introduction`, `/docs/web-search`, `/docs/web-for-ai`, `/docs/answer-v2`, `/api`. Docs site is Zudoku.

## Headline findings (they change the design)

1. **`count` only accepts `10`.** Live: every value except 10 (0,1,2,5,11,20,50,51,100; GET and POST) returns `400 {"message":["count must be equal to 10"],...}`. Bisecting the 1..50 range: only 10 works. Docs say the same ("Fixed at 10"). To offer 1..50 the MCP must emulate: paginate with `offset` (0/10/20/30) and slice. Max realistic yield about 40 results (offset 30 is valid and returned 10 for "weather"; 0 for "python programming"). count 41..50 cannot be fully satisfied; best effort, return what exists.
2. **Pages are often short and can overlap.** A "count=10" page returns 7-10 results (dedup/filtering). Over 4 pages for "weather": 38 results, 36 unique URLs. Emulation should dedupe by URL and keep fetching pages until enough or an empty page / offset > 30.
3. **The Answer endpoint returns `403 {"message":"Forbidden","statusCode":403}` for our key**, for every body (even invalid ones, so authz runs before validation). Docs: "Answer API is currently available by invitation only. Contact support@staan.ai to be allowlisted." The Answer response shape could not be verified live; below is from docs + OpenAPI + SDK. Tests must use mocks (respx), and the tool should surface 403 as a clear "key not allowlisted for Answer" error.
4. **`extra_snippets=true` never returned `extra_snippets` live** (GET/POST, min_score 0..0.2, max_snippets 1..5, with/without full_content, en-us/en-gb; results had no `extra_snippets` key at all, no `published_date` either). Docs promise `[{chunk, score}]` (or `[]` on fetch failure). Treat as optional/possibly absent; code must handle missing key, `[]`, and list of `{chunk, score}` objects. (Possible tier restriction: staan.ai pricing says extra snippets belong to "Web Search for AI" plan.) `full_content` DOES work on our key.
5. **Only 12 markets are accepted** (server-side enum), although docs prose says only fr-fr/en-us/de-de. Case-insensitive on input (`EN-GB`, `fr-FR` accepted; the API echoes lowercase).
6. Both live and docs: HTTP 200 for GET, **201 for POST** on `/search/web`.

## Base URL, auth

- Base: `https://api.staan.ai/v2`
- Header: `Authorization: Bearer <API_KEY>` (required on all endpoints).
- Keys are created in the Staan console (multiple keys allowed per FAQ). No OAuth: `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource`, `/.well-known/openid-configuration` on api.staan.ai all 404; docs never mention OAuth. Conclusion: static Bearer key only, so use `STAAN_API_KEY` env var, as the user's fallback plan says.
- Missing or wrong key: `401 {"message":"Unauthorized","statusCode":401}` (identical for search and answer).
- Only endpoints that exist: `GET|POST /search/web`, `POST /answer`. Probed 404 (`{"message":"Cannot GET /v2/...","error":"Not Found","statusCode":404}`): `/search`, `/search/news`, `/search/images`, `/answer` (GET), `/models`, `/usage`, `/health`, `/openapi.json`, `/docs`. There are no separate news/images endpoints (staan.ai marketing mentions "News" but it is the same web endpoint).

## Endpoint 1: Search web

`GET https://api.staan.ai/v2/search/web` (200) and `POST https://api.staan.ai/v2/search/web` (201, JSON body). Same behaviour; arrays `include_domains` / `exclude_domains` are POST-only. operationId `searchWebV2_v2` / `searchWebV2Post_v2`. Unknown params are silently ignored (GET and POST).

### Request parameters

| name | type | default | constraints (docs, then live-verified) |
|---|---|---|---|
| `q` | string, required | none | min length 1 (empty -> 400 `q must be longer than or equal to 1 characters`). Docs: max 400 chars; **live: 401 chars accepted (200)**, so not enforced. Supports `site:` / `-site:` operators, `site:a.com OR site:b.com`. Operators are stripped before the engine sees the query; `query.q` echoes original. |
| `market` | string enum | `fr-fr` | `fr-fr, de-de, en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz, en-za, en-sg`. Case-insensitive. Others -> 400 `market must be one of the following values: fr-fr, de-de, en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz, en-za, en-sg`. Rejected live: es-es, it-it, nl-nl, pt-pt, en-xx, en_GB, en, fr-be, fr-ca, de-at, de-ch, ja-jp, zh-cn. |
| `count` | number | 10 | must equal 10 (see headline 1). Numeric string "10" accepted in POST body. |
| `offset` | number | 0 | multiple of 10, 0..30. `-1` -> 400 `["offset must be a factor of 10","offset must not be less than 0"]`; 40 -> 400 `offset must not be greater than 30`. |
| `extra_snippets` | boolean | false | Triggers fetch + chunk + rerank. GET: string "true"; junk value "maybe" silently accepted. See headline 4. |
| `full_content` | enum `markdown` \| `html` | absent | Anything else (incl. empty string, `text`) -> 400 `full_content must be one of the following values: markdown, html`. Triggers fetch + rerank; result order then reflects reranking, not SERP order. |
| `max_snippets` | number | 3 | 1..10 (0 -> 400 `max_snippets must not be less than 1`; 11 -> 400 `must not be greater than 10`). Only meaningful with `extra_snippets=true`; accepted otherwise. |
| `min_score` | number | 0.1 | 0..1 (2 -> 400 `min_score must not be greater than 1`). The MCP must NOT expose it (user requirement); simply never send it. |
| `include_domains` | string[] (POST) | absent | max 10 (docs say extras dropped; **live: 11 -> 400 `include_domains must contain no more than 10 elements`**). Entries must be bare domains: `www.python.org` and `https://www.python.org/x` -> 400 `include_domains contains an invalid domain`; `python.org` OK. |
| `exclude_domains` | string[] (POST) | absent | same limits. Sending both -> 400 `include_domains cannot be used with exclude_domains`. |

Validation error format (NestJS class-validator): `{"message":["<msg1>","<msg2>"],"error":"Bad Request","statusCode":400}` (`message` is an array).

### Response (200/201)

Live (no enrichment) example:

```json
{
  "search_id": "be928ed14feb766204cf6109e7e41c92",
  "query": {"q": "open source llms", "market": "en-us", "count": 10, "offset": 0},
  "web": {"results": [{
    "title": "A list of open LLMs available for commercial use.",
    "url": "https://github.com/eugeneyan/open-llms",
    "snippet": "A list of open LLMs available for commercial use. 2048 Apache 2.0. ...",
    "display_url": "https://github.com/eugeneyan/open-llms",
    "hostname": "github.com",
    "favicon_url": "https://s.qwant.com/v1/fav/..."
  }]}
}
```

Schema `SearchWebResponse` (required: search_id, query, web):
- `search_id` string (live: 32-hex; docs say UUID v7).
- `query`: `q` string, `market` string, `count` number, `offset` number (all required), `altered_query` string (optional, only if engine rewrote query). `query.count` always echoes 10 even if fewer results returned.
- `web.results[]` (`SearchWebResult`): required `title`, `url` (uri), `snippet`, `display_url`, `hostname`; optional `favicon_url`, `thumbnail` (uri), `extra_snippets`, `full_content`, `published_date` (ISO 8601 date-time, e.g. `2026-08-03T00:00:00Z`; only when a fetch happened and a date resolved).
- `extra_snippets` (docs): array of `{chunk: string, score: number 0..1}` descending by score; `[]` when fetch failed or nothing above min_score. **Note: items are objects, not strings**, so `snippet (...) extra_snippets` joining must use each `.chunk`.
- `full_content`: object `{text: string, format: "markdown"|"html", length: number}`. On fetch failure field is present with `"text": ""`, `"length": 0` (observed live: several results with length 0 or 80). Live samples: markdown lengths 0..73725; html up to 581645 (large, so consider truncation/size guard in MCP).
- `confidence` (`{score, dispatcher:{decision, version, threshold_validation}}`) is in the OpenAPI but was **never present live**. Ignore.
- Optional-ness: results count varies 7-10 per page (see headline 2).

Latency live: plain search about 0.7-1.4 s; `full_content=markdown` about 3.6 s, html 1.5 s (docs recommend 8-10 s client timeout when enriched). Enrichment fetch timeouts: 3.5 s per URL, 4 s global.

### Limits

- Rate limit: 20 req/s ("excess traffic is rejected", status/format not documented). Live burst of 45 concurrent GETs: all 200, no 429, no `Retry-After` / `RateLimit-*` headers. Implement backoff on 429/5xx anyway.
- Pricing (staan.ai): Web Search EUR 1/1,000 req; Web Search for AI (extra snippets, full content) EUR 2/1,000 req; first 1,000 requests per month free; Answer = custom pricing. (docs intro says "from EUR 2".) **Each wildcard-market fan-out and each offset page is a billable request**, so keep expansion bounded and document it.
- Each call returns 10 documents (FAQ).
- Response headers: `x-response-time`, `x-envoy-upstream-service-time`, `server: istio-envoy`; nothing else useful.

## Endpoint 2: Answer

`POST https://api.staan.ai/v2/answer` (operationId `answerV2Endpoint_v2`), `Content-Type: application/json`. Status: **invitation-only** (403 without allowlisting; not verifiable live with our key). Documented responses: 200 (JSON, or `text/event-stream` when `stream=true`), 401, 500 (docs list only 401/500 in the error table; 403 seen live; 400 validation likely same NestJS shape, unverified).

### Request body (`AnswerRequest`, required: `query`)

| name | type | default | notes |
|---|---|---|---|
| `query` | string | none | required, 1-2,000 chars |
| `filter` | string | none | "Canonical domain filter for search (without www or URL scheme)"; ignored when `sources` set. Example `frandroid.com`. (MCP must normalise: strip scheme, `www.`, and per user also path.) |
| `stream` | boolean | false | SSE. MCP: always false, do not expose |
| `history` | `{role: "user"\|"assistant", content: string}[]` | none | max 20 messages, strictly alternating user first, user content <= 2,000 chars, assistant <= 10,000. MCP: do not expose (user requirement) |
| `mode` | enum `short`,`long` | `short` | response length |
| `language` | enum `fr`,`en` | **`fr`** | answer language. MCP should default to `en` (or expose) since API default is French |
| `markdown` | boolean | false | MCP: always true (user requirement; needed so GFM footnote citations read well) |
| `related_queries` | boolean | false | up to 5 (OpenAPI) / "four" (guide) follow-up queries; not needed in simple_response |
| `query_rewrite` | boolean | true | ignored when `sources` set |
| `sources` | `{url?, content?, title?}[]` | none | max 10; bypasses search; `content` max 50,000 chars |
| `sources_mode` | enum `search`,`context` | `search` | `context` injects content directly (first 5,000 chars per source), no fetch/rerank |

### Response 200 (`AnswerResult`; all six fields required)

```json
{
  "request_id": "01938fc2-1b3a-7e4d-9c12-a4f8e3c20d91",
  "answer": "Les dernieres avancees en fusion nucleaire incluent...",
  "citations": [{"position": 142, "source_id": "1"}, {"position": 310, "source_id": "3"}],
  "sources": [{"id": "1", "url": "https://www.cea.fr/...", "title": "CEA - Fusion nucleaire"}],
  "related_queries": ["How does ITER work?"],
  "usages": [{"step": "query_rewrite", "input_tokens": 95, "output_tokens": 12},
             {"step": "answer", "input_tokens": 2840, "output_tokens": 194}]
}
```

- `citations[]`: `{position: number, source_id: string}`. `position` is documented only as "inline citation marker"; it is almost certainly a **character offset into `answer`** where the marker belongs (142, 310 in a paragraph-length answer), and `source_id` matches `sources[].id` (string ids "1","2",...). **Unverified** (no live access). The GFM conversion (`[^n]` at position, footnote defs `[^n]: url` per source) must be built defensively: clamp positions to `[0, len(answer)]`, insert from the highest position downward so offsets stay valid, dedupe repeated (position, source) pairs, ignore citations whose source_id is unknown, and number footnotes by first appearance. Note the sample lists source 2 as not cited (citations only reference 1 and 3). Whether the model text also contains its own inline markers (e.g. `[1]`) is unknown; handle both (do not double-insert if text already has `[n]`; hard to know, so add a mockable test both ways and a fallback: if `citations` empty, append nothing).
- `usages[].step` enum: `answer`, `query_rewrite`, `related`.
- `related_queries` present (array) even when not requested (likely `[]`).

### Streaming (not to be supported, for reference)

`text/event-stream` events in order: `sources` (`[{id,url,title}]`), `assistant` (JSON string chunk), `citation` (`{reference_ids: number[]}`), `related` (`{related_queries}`), `usage` (`{step,input_tokens,output_tokens}`), `done` (`{finish_reason}`), `usages` (array after done). Format: `event: <name>\ndata: <json>\n\n`. Streaming citations are index-based (`reference_ids` numbers), unlike the non-stream `position` offsets.

### Errors

| status | body | when |
|---|---|---|
| 401 | `{"message":"Unauthorized","statusCode":401}` | missing/invalid key (search and answer) |
| 403 | `{"message":"Forbidden","statusCode":403}` | Answer not enabled for key (live, always) |
| 400 | `{"message":[...],"error":"Bad Request","statusCode":400}` | validation (search verified live; answer unverified) |
| 404 | `{"message":"Cannot GET /v2/x","error":"Not Found","statusCode":404}` | unknown route/method |
| 500 | not documented | internal error |
| 429 | not documented / not observed | rate limit ("rejected") |

Note `message` is a string for 401/403/404 and a list of strings for 400: the client error parser must handle both.

## Design implications for the MCP (recommendations to the implementer)

1. Search `count` 1..50: implement via offset pagination + slicing + URL dedupe, sending `count=10` upstream always. Cap at what the API can give (offset <= 30 means at most 40 raw results). Decide and document behaviour for count > available (return fewer, not an error). Cost note: count 1..10 = 1 request per market, 11..20 = 2, 21..30 = 3, 31..50 = 4.
2. Markets: canonical set (12) above; wildcard expansion (`en-*`, `en*`, `*-fr`, plus lists) is matched case-insensitively with fnmatch against that set. `en-*` -> en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz, en-za, en-sg (10 requests); `*-fr` -> fr-fr, en-fr; `en*` same as `en-*`. Zero matches must be a validation error listing valid markets. Dedupe expanded list; preserve order. Use `POST`? GET is enough (no domain arrays needed unless we expose include/exclude; if exposed, use POST and reject sending both).
3. Default `market="en-gb"`, `full_content="markdown"` (user requirement). Warn in docs that full_content adds ~3-4 s latency and huge payloads (html up to 580k chars); consider a per-result truncation option. `full_content` with `length: 0` / empty text should be omitted from simple output (optional field).
4. `simple_response` search item: `{title, url, snippet, full_content?}`. `snippet` = `snippet` if no extra snippets else `" (...) ".join([snippet, *(e["chunk"] for e in extra_snippets)])`. Since extra_snippets objects are dicts with `chunk`, accept plain strings defensively too. `full_content` in simple output should be the text string (`full_content.text`), only when non-empty. Never expose `min_score`; do not send it.
5. Answer: always send `stream=false`, `markdown=true`, no `history`. Normalise `filter`: lowercase, strip scheme, `www.`, path/query/port/trailing slash; validate result is a bare domain. Default `language`: the API defaults to `fr`, so the MCP default should likely be `en` (user wants English-centric defaults) and be overridable (`fr`/`en`).
6. Handle 403 on Answer with a helpful message (contact support@staan.ai for allowlisting), 401 -> check `STAAN_API_KEY`, 400 -> join `message` list, 429/5xx -> retry with exponential backoff + jitter (documented advice), timeouts >= 10 s for enriched search, longer (30-60 s) for Answer.
7. Live tests: only search is runnable end-to-end; answer must be mocked. Sample real responses are saved in `.tmp/staan-api-docs/sample_*.json` (search with full_content markdown; es+fc). `sample_*` are scratch and can be used as test fixtures (trim them first, they are large).
8. Auth: no OAuth possible on the upstream side. For the MCP HTTP transport, one can still protect the MCP server itself via the MCP SDK's `TokenVerifier` / auth settings (resource-server role) with static or JWT tokens, but upstream credential stays `STAAN_API_KEY`. (Researching MCP-side OAuth belongs to the MCP-sdk research topic.)

## Raw artefacts (scratch, in `.tmp/staan-api-docs/`)

- `openapi.json` reconstructed OpenAPI 3.1.1 (from docs bundle)
- `probe1.py`..`probe8.py` live probes (read key at runtime; no key in outputs), `sample_*.json` response samples, `page_*.html`, `sitemap.xml`
