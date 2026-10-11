# Research gaps: resolved facts and open questions

Written 2026-09-30 after reading all of `docs/research/*`. 20 live API calls were made (search + answer, budget was 25), scripts in `.tmp/research-gaps/` (`live1.py`..`live4.py`, `sdk_shape.py`; the key is read at runtime and never printed). Extra sources read: `Qwant/answer-node` (official TS SDK: README, `src/types.ts`, `src/index.spec.ts`, examples) and `Qwant/playground-answer-V2` (official React playground: `useChat.ts`, `ChatMessage.tsx`), plus the saved docs pages in `.tmp/staan-api-docs/`.

## A. Contradictions between the earlier notes, and their resolution

| Topic | staan-api-docs.md | staan-api-live.md | Resolved (live, 2026-09-30) |
|---|---|---|---|
| 401 body | `{"message":"Unauthorized","statusCode":401}` for missing or wrong key | wrong key gives `{"code":"INVALID_CREDENTIALS","message":"Unknown API key or invalid secret"}` | Both are right. Wrong key -> `INVALID_CREDENTIALS` shape, missing header -> `Unauthorized` shape. Error parser must accept both (`message` str or list, optional `code`). |
| Query longer than 400 chars | "live: 401 chars accepted" | "silently truncated to 400" | Compatible: accepted (200) and truncated by the API. Docs prose says "rejected"; it is not. Client should validate 1..400 itself anyway (truncate or error, our choice; recommend a clear ToolError for empty, truncate-with-warning is not needed, just pass through). |
| `count` | only 10 | only 10 | Re-verified this session: GET and POST, `count=1` and `count=50` -> `400 {"message":["count must be equal to 10"]}`. Both requested edges are unsupported, only 10 is. Bisect is moot. |
| extra_snippets | never returned | never returned | Re-verified with the docs' exact curl examples (GET `extra_snippets=true&max_snippets=5&min_score=0.2`; POST with `min_score 0`, `en-us`, with and without `full_content`): 200/201, 10 results, result keys only `display_url, favicon_url, hostname, snippet, title, url` (+ `full_content`, `published_date` when full_content is set). The feature is dead for our key (probably plan-gated or disabled). |
| Answer 403 | 403 always | 403 always | Re-verified: plain body and `sources`+`sources_mode=context` body both `403 {"message":"Forbidden","statusCode":403}`. Not fixable from our side (needs allowlisting by support@staan.ai). |
| MCP SDK import path | `from mcp.server import MCPServer` | `mcp.server.mcpserver.MCPServer` | Same class, both paths valid in mcp 2.2.0. Use `from mcp.server.mcpserver import MCPServer, Context`. |
| User said "npm package" | n/a | n/a | Clearly means a PyPI package (everything else in the request is Python). Treat as PyPI + GitHub. |

## B. Resolved facts (safe to implement)

### B1. Search: count 1..50
- Upstream only accepts `count=10` (always send 10, or omit). Offsets 0/10/20/30 only, so at most 4 pages, 40 raw results per query and market.
- Paging behaviour re-measured, en-gb, 4 pages each: `"staan search api"` -> page sizes [7, 10, 10, 10], 37 results, 33 unique URLs; `"best pizza in london"` -> [10, 10, 10, 10], 40 unique. Earlier: `"python programming"` offset 30 gave 0.
- So: pages are short and overlap. Algorithm to implement (per market):
  1. Fetch `ceil(count/10)` pages concurrently (offsets 0,10,...; cap offset at 30).
  2. Concatenate in page order, dedupe by exact `url` (keep first).
  3. While `len(unique) < count` and next offset <= 30 and the last page was not empty: fetch the next page sequentially and dedupe again.
  4. Slice to `count`. Fewer than `count` is a valid outcome, not an error (`count` 41..50 can never be filled, effective ceiling about 38-40).
- Cost: every page is a billable request (free tier 1,000/month). count 1..10 = 1 request per market, 11..20 = 2, 21..30 = 3, 31..50 = 4 (+ short-page top-ups). The tool schema still accepts 1..50 as the user required; document the ceiling.

### B2. Markets and wildcard expansion
- Exactly 12 valid markets: `fr-fr, de-de, en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz, en-za, en-sg`. Case-insensitive on the API side. Bare `en`, `en_GB`, `fr-ca`, `es-es` etc. are rejected with 400.
- Wildcard matching, verified with `fnmatch.fnmatchcase` against the lowercased pattern (do NOT use `fnmatch.fnmatch`: on Windows it applies `normcase`, behaviour differs by OS):

  | pattern | expands to |
  |---|---|
  | `en-*`, `en*`, `EN-*` | en-us, en-gb, en-fr, en-ca, en-au, en-in, en-ie, en-nz, en-za, en-sg (10) |
  | `*-fr`, `??-fr` | fr-fr, en-fr |
  | `fr*` | fr-fr |
  | `e?-gb` | en-gb |
  | `*`, `*-*` | all 12 |
  | `es-*` | none -> validation error listing the 12 valid markets |
- Rules to implement: accept `str | list[str]`; strip whitespace; lowercase; validate each pattern against `^[a-z*?-]+$` (rejects `[`...`]` fnmatch classes and junk before matching); a non-wildcard token must be an exact member of the 12 or it is an error (do not silently drop); expand per token, dedupe preserving order of first appearance; empty list -> default `en-gb`; zero matches for any token -> error naming that token. `en-gb` order in the canonical list is used for pattern expansion.
- Fan-out guard: `*` with count 50 = 12 x 4 = 48 requests (about 5% of the free monthly quota). Recommend a configurable cap, e.g. `STAAN_MCP_MAX_REQUESTS_PER_CALL` default 24, and refuse (clear error) when `markets x pages` exceeds it. Concurrency semaphore about 8 (rate limit is 20 req/s, no 429 ever observed).
- Optional, cheap future-proofing: the invalid-market 400 message lists the currently valid markets; the list could be re-discovered from it. Not needed for v1; hard-code the 12 and keep the constant in one place.

### B3. `full_content`
- Values `markdown` | `html` only, case-sensitive (`MARKDOWN`, `text`, `none`, empty all 400). Default `markdown` (user requirement). To turn it off, omit the parameter. Recommendation: the MCP schema `full_content: Literal["markdown","html","none"] = "markdown"` where `none` means "do not send" (small extension beyond the request; needed because otherwise there is no way to get a fast, cheap plain SERP).
- Response object: `{text, format, length}`. Emit `full_content` in simple output as the plain `text` string only when `text` is non-empty (`length` can be 0 or a junk stub of about 80 chars; `length` is unreliable, check `text`).

### B4. Answer `filter`
- API contract (docs + SDK type doc): "canonical domain filter, without www or URL scheme", example `frandroid.com`, `lemonde.fr`; single string, not an array; ignored when `sources` is set (we do not expose `sources`, so it is always effective).
- The search endpoint's `include_domains` is strict (`https://www.x.com/` -> 400 "invalid domain") which shows the platform validates domains, so normalise defensively. Implement `normalize_domain(value)`: strip, lowercase, drop scheme (`^[a-z][a-z0-9+.-]*://`), drop userinfo, path, query, fragment, port and trailing dot, drop leading `www.`; result must match a bare hostname regex with at least one dot (IDN: convert with `idna`/`str.encode("idna")` or reject); otherwise a ToolError. Examples: `https://www.LeMonde.fr/politique?x=1` -> `lemonde.fr`; `www.frandroid.com` -> `frandroid.com`; `http://user@example.com:8080/a` -> `example.com`; `` (empty or whitespace) -> treated as omitted. Only a leading `www.` is stripped (`www2.example.com` stays).

### B5. Answer simple_response: citation format (best evidence, still unverified live)
Evidence gathered (docs page, official SDK, official playground):
- Non-stream: `citations: [{position: number, source_id: string}]`, `sources: [{id: string, url, title}]`. Docs example: answer text of paragraph length, positions 142 and 310, source ids `"1"`, `"3"`, with source `"2"` uncited. The docs call them "Inline citation markers".
- Stream (`citation` event, "Source references at the current position"): the event is emitted between two `assistant` text chunks, so a citation marks the point after the text emitted so far. This strongly supports: **`position` = insertion index into `answer` (the marker goes after `answer[:position]`)**. The playground confirms the answer text itself carries no markers: it builds the text by concatenating `assistant` deltas and injecting its own `[^cite:<id>]` marker at each `citation` event, then renders GFM with remark-gfm. That means the model text is marker-free (at least in stream mode), so no double-insert risk is expected.
- The official SDK mock uses `{position: 0, source_id: "src_1"}` with sources id `"src_1"`: ids are opaque strings, do not assume numeric. Docs: `"1","2","3"`. The playground shows the label as `parseInt(id)+1` and looks sources up by `s.id === id`, which would imply 0-based ids; this contradicts the docs sample (1-based). Irrelevant if we key strictly on `sources[].id == source_id` and number footnotes ourselves.
- Unknown: whether `position` counts Python code points, UTF-16 code units (backend is likely JS/NestJS) or bytes. Identical for BMP-only text; differs after emoji/astral characters.

Implement `to_gfm(answer, citations, sources)` as one small pure function (easy to swap the offset semantics later):
1. Build `id -> url` from `sources` (`str(id)`). Ignore citations whose `source_id` is unknown; fallback: if the id is a decimal string not found and `1 <= int(id) <= len(sources)` use `sources[int-1]` (guards the 0/1-based ambiguity).
2. Sort citations by `(position, original order)`. Clamp `position` into `[0, len(answer)]` (out-of-range means "end"). Offset unit: treat as UTF-16 code units when text has astral characters? Recommendation: treat as Python str index (code points) in v1, isolate behind a helper `_index_for(answer, position)`, and switch to a UTF-16 mapping if live access shows otherwise.
3. Number footnotes by first appearance in text order (1, 2, ...); same source cited again reuses its number (repeated `[^1]` is valid GFM). Several citations at one position become `[^1][^2]` in order; dedupe identical (position, source) pairs.
4. Insert markers from the highest position downward so earlier offsets stay valid.
5. Append `\n\n[^1]: <url>\n[^2]: <url>` for cited sources only (as in the user's example; uncited sources are omitted). If nothing was cited, return the answer unchanged with no definitions block. Use the source URL as the definition text (user example: `[^1]: http://google.com`); the title is dropped.
6. Never mutate the answer when `citations` is empty or `sources` is missing.

### B6. MCP tool result wire shapes (verified with mcp 2.2.0, `.tmp/research-gaps/sdk_shape.py`)
The MCP spec allows `structuredContent` only as a JSON object, and the SDK wraps a returned `list[...]` as `{"result": [...]}`. To return the bare JSON array requested for search `simple_response`:

| tool return | outputSchema | content items | structuredContent |
|---|---|---|---|
| `-> CallToolResult(content=[TextContent(text=json.dumps(items))])` | none | 1 item, text is exactly the JSON array | none |
| `-> str` (JSON string) with `structured_output=False` | none | 1 item, same text | none |
| `-> list[dict]` with `structured_output=False` | none | one TextContent PER ITEM, pretty-printed | none |
| `-> dict[str,str]` (answer) | `{"additionalProperties":{"type":"string"},"type":"object"}` | 1 item, pretty JSON `{"answer": ...}` | `{"answer": "..."}` |

Decision: search `simple_response=true` -> return `CallToolResult` with a single TextContent holding the compact JSON array (exact shape asked for); `simple_response=false` -> the raw response(s) in the same way. Answer `simple_response=true` -> `dict[str, str]` return `{"answer": ...}` (gets an output schema and structuredContent for free). Use `CallToolResult` for errors only via `ToolError` (never return an error string). Annotate return types so mypy strict passes (`-> CallToolResult`).

### B7. Auth
- No OAuth upstream (re-confirmed by mcp-oauth.md). Final design: `STAAN_API_KEY` env for the upstream key in all modes; optional OAuth 2.1 resource-server gate for http/stateless (TokenVerifier), never forwarding the inbound token. Nothing left to research.

## C. Design decisions the spec left open (not blocking, recommendations made)

1. **Multi-market response shape.** The requested simple format is a flat array with no market field. Recommendation: flat array, concatenated in market order, deduped by `url` across markets (first occurrence wins), no market attribution. `simple_response=false` returns `{market: <raw response or merged raw pages>}` so attribution is available when wanted. Alternative (rejected): dict keyed by market would break the requested format.
2. **`count` with several markets.** Recommendation: `count` is per market, applied before cross-market dedupe (so the total can be up to `markets x count`). Document it in the tool description and in the cost note.
3. **Partial failure across markets.** Recommendation: if every market fails, raise ToolError with the first error; if some succeed, return the successful results and log the failures to stderr (stdlib logging) so the array shape stays valid. In `simple_response=false` mode include an `errors` map. Zero results overall is a valid `[]`.
4. **Send `extra_snippets=true`?** The requested snippet format joins `extra_snippets` with `" (...) "`, so the feature must be requested to ever be populated. Recommendation: expose `extra_snippets: bool = True`, send it, and treat the field as optional (absent, `[]`, list of `{chunk, score}`, or plain strings). It is a no-op on our key today, and full_content=markdown already puts the request in the "for AI" pricing tier and triggers the fetch/rerank, so the default costs nothing extra. When `full_content="none"` and `extra_snippets=true`, latency rises (fetch); document it. `min_score` and `max_snippets` are never sent (server defaults 0.1 and 3).
5. **Answer parameters to expose** (user only excluded stream, history, markdown): `query` (1..2000), `filter`, `mode` (`short|long`, default `short`), `language` (`fr|en`, API default is `fr`; recommend MCP default `en`, consistent with the `en-gb` search default), `related_queries` (only useful with `simple_response=false`), `query_rewrite`, `simple_response`. Do not expose `sources` and `sources_mode` in v1 (out of scope, adds injection surface). Always send `stream=false`, `markdown=true`.
6. **`simple_response=false`** returns the upstream JSON (search: per-market raw responses; answer: raw `AnswerResult`) minus nothing; key never appears in it.
7. **Timeouts**: search 15 s plain, 30 s with `full_content`/`extra_snippets`; answer 60 s. Retry (max 2-3, exponential backoff + jitter, honour `Retry-After`) only on 429, 502, 503, 504 and connection errors; never on 4xx. GET for search (no domain arrays exposed; `site:` operators in `q` work and are stripped by the API). Accept any 2xx (GET 200, POST 201).
8. **Tests must mock the API** (`httpx2.MockTransport` per packaging notes): populated `extra_snippets`, Answer success and error, 401 in both shapes, 429, short pages, overlapping pages. Only search has live smoke tests (opt-in via env, skipped in CI).

## D. Remaining unresolved (cannot be settled without Answer access or maintainer input)

1. **Answer response shape and citation semantics are unverified live** (403 for our key). Working assumption is B5 (position = insertion index, marker-free answer text). Needs: ask support@staan.ai to allowlist the key, then run one call with `markdown=true` and confirm (a) the offset unit, (b) marker-free text, (c) `source_id` matches `sources[].id`, (d) 0- or 1-based ids, (e) whether the 400 validation shape matches search.
2. **Answer error semantics** other than 401/403/500 (400 shape, 429, 5xx bodies) are undocumented; parser is defensive.
3. **`extra_snippets` populated shape** is documented only (`[{chunk, score}]`); never observed. Possibly a plan restriction. Ask Staan whether the key's plan includes "Web Search for AI".
4. **429 behaviour** (status, body, `Retry-After`) never triggered; documented limit 20 req/s.
5. **Market list stability**: 12 markets today (docs prose still says 3). If Staan adds markets, the hard-coded list needs a release; the 400 message is the only discovery source.
6. **Deep pagination ceiling**: some queries return 0 at offset 30; count above about 30 is therefore best effort (no fix possible).
