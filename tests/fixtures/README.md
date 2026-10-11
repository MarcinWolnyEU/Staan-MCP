# Test fixtures

- `search_*.json` are real `POST/GET /v2/search/web` response bodies captured during research
  (headers dropped). Long texts (`full_content.text`, `extra_snippets[].chunk`) were truncated to
  at most 600 characters; `full_content.length` still holds the upstream length. They contain no
  credentials.
  - `search_plain.json`: ten results, no enrichment.
  - `search_full_content.json`: `full_content` as markdown, one result with an empty `text`.
  - `search_extra_snippets.json`: `extra_snippets` as `[{chunk, score}]`, one result with `[]`.
  - `search_short.json`: a page with fewer than ten results.
- `answer_*.json` are written by hand from the documented `AnswerResult` shape
  (`docs/research/staan-api-docs.md`); the Answer endpoint is not reachable with our keys.
  - `answer_normal.json`: two cited sources, one uncited.
  - `answer_repeated_citations.json`: a source cited twice, several sources at one position and
    an exact duplicate citation.
  - `answer_unknown_sources.json`: unknown ids, plus a 1-based fallback id (`"2"`).
  - `answer_no_citations.json`: sources listed, `citations` empty.
