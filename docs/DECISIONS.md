# Decisions log (for future maintainers / agents)

Research notes live in `docs/research/`. Scratch work lives in `.tmp/<topic>/` (gitignored).

## Working conventions
- Do not use the Claude memory system for this project; persist notes here in `docs/`.
- Never commit api keys. Never write key values into files.
- WebFetch is sometimes blocked by bot protection; a real browser is available via the
  `mcp__browserplug__*` tools (open your own background tab).
- The system Python is 3.8, so use `uv` for everything.
- The user asked for an "npm package" but meant a **PyPI** package.
- Orchestrate with subagents.

## Product decisions (2026-09-30)
- Auth: `STAAN_API_KEY` from the environment is the only upstream credential. HTTP modes bind to
  loopback by default. There is an optional OAuth 2.1 resource-server gate (JWT/JWKS or
  introspection against an operator-run AS, audience + scope checked, RFC 9728 metadata). The
  server never forwards inbound tokens upstream, per the MCP spec's token-passthrough ban.
  Staan itself has no OAuth.
- Transports: stdio (default), http (streamable HTTP), stateless (streamable HTTP with
  stateless_http=True).
- Search `count`: the API accepts only `count=10` (confirmed with both keys). It is NOT exposed
  and is always sent as 10 (the user's decision).
- Search `market`: default `en-gb` (the API itself defaults to fr-fr). Accepts a string or a list,
  with fnmatch wildcards expanded against the 12 supported markets and one request per market.
- Search `full_content`: default `none` (it was `markdown` until 2026-10-11, see the decision of
  that date). `min_score` is never exposed or sent.
- Answer: stream=false, no history, markdown=true always. `filter` is normalised to a bare domain
  (scheme, www., path, port and userinfo stripped). `language` defaults to `en`.
- `simple_response` (default true) on both tools. See the README for the output shapes.

## Decisions of 2026-10-05 (user answers)
- The Answer endpoint stays 403 for every key we have, so it is implemented from the documentation
  only and tested with mocks.
- Search exposes `offset` (0/10/20/30), `include_domains`/`exclude_domains`, and `full_content`
  (markdown|html|none). `extra_snippets` is NOT exposed: the server always sends
  `extra_snippets=true`, and `max_snippets`/`min_score` are never sent.
- Multi-market search: simple_response is one flat list, interleaved round-robin by rank, deduped
  by URL. The full response is a list of raw per-market responses (or `{market, error}`). Partial
  failures are tolerated; the tool errors only if every market fails or if an
  auth/disabled/forbidden error occurs.
- Answer exposes `language` (en|fr, default en), `mode` (short|long), and `sources`/`sources_mode`.
  It does not expose `related_queries`, `history`, `stream` or `markdown`.
- No per-request client key header (`X-Staan-Api-Key`) and no static bearer gate. The only HTTP
  protection is loopback binding or the optional OAuth RS gate.
- Publishing: local git repo only. Nothing is pushed and nothing is published to PyPI from this
  session.
- Testing: pytest + ruff are the primary tools the user named. mypy, pylint, radon/xenon and
  mutmut stay as additional gates.
- The full interface contract is in `docs/DESIGN.md`.

## Review fixes (2026-10-05)
- `STAAN_MCP_MAX_CONTENT_CHARS` defaults to **off** (0). The review found that the default
  `full_content=markdown` output is 115 to 170 thousand characters for one market, which exceeds
  Claude Code's default 25k-token tool-output limit, but a default cap would silently shorten what
  the user asked for ("full_content must default to markdown"). So the knob exists, is documented in
  the tool description and README, and the default is left for the maintainer to decide.
- `extra_snippets` stays always on (decision of 2026-10-05). The docs therefore say `none` shrinks
  the response; they no longer say faster or cheaper.
- Read and write timeouts are not retried (billable POST); the whole call has a deadline
  (`STAAN_DEADLINE`, 50 s). Jitter and a `Retry-After` clamp were not added.
- Numeric `source_id` fallback only when the sources carry no ids at all.
- Citation placement avoids code fences and tables. Link destinations and inline code spans are
  not handled (the API's position semantics are unverified because Answer returns 403).
- `answer` fails with a tool error when the upstream response has no `answer` text, in both modes,
  because the declared output schema requires it.
- Introspection has no cache, negative cache or concurrency limit (optional in the review);
  it has a shared connection pool and a local token plausibility check.
- Origin-only issuer and resource URLs are accepted with and without the trailing slash, rather
  than canonicalising one spelling: the AS's real `iss` is out of our control, and the SDK
  publishes the slashed form.

## Decision of 2026-10-11 (user)
- `full_content` defaults to `none`. The `markdown` default produced 115 to 170 thousand characters
  per single-market call, which clients can truncate or reject. Callers who need page text ask for
  `markdown` or `html`. `STAAN_MCP_MAX_CONTENT_CHARS` stays off by default and now only affects
  those calls. The enum order in `contract.FULL_CONTENT_CHOICES` is `none, markdown, html`
  because the first value is the default.

## Packaging and CI review fixes (2026-10-05)
- `CHANGELOG.md` stays hand-written (Keep a Changelog, as DESIGN.md says). Commitizen's
  generator cannot parse that layout (verified: `cz bump` deleted the hand-written sections), so
  `update_changelog_on_bump = false` and the maintainer moves the Unreleased notes before
  `cz bump` (CONTRIBUTING.md). Verified with a two-release simulation plus an `rc` bump in
  `.tmp/review-packaging-fix/sim`: the bump commits touch only `pyproject.toml`, `uv.lock` and
  `server.json`.
- Versions and tags are PEP 440 (commitizen's default scheme; uv normalises to it), so the image
  tags use metadata-action `type=pep440`, and pre-release detection in `release.yml` is a regex
  on the normalised version (`(a|b|rc)N` or `.devN`), tested against `packaging`.
- Pre-releases are not published to the MCP Registry (it would show them as the latest version).
- `release.yml` calls `ci.yml` through `workflow_call` instead of checking the commit status of
  `CI passed`: no token or API polling is needed and the exact tagged tree is tested. The caller
  grants `id-token: write` because the called codecov job declares it (it is skipped with
  `release: true`). The CI build artifact is called `ci-dist` so it does not clash with the
  release's `dist` artifact in the same run.
- pre-commit.ci is not used: there is no way to turn its autoupdate off, and without `--freeze`
  it replaces the SHA pins with tags. The hooks run in the `metadata` job of `ci.yml` instead,
  and Dependabot owns hook revisions.
- `mcp-publisher` v1.8.1's SHA-256 is pinned in the workflow; it came from a download whose
  sigstore bundle verified against `modelcontextprotocol/registry` at `refs/tags/v1.8.1`.
- pip-audit lives in its own `audit` group (not part of `dev`), so it is hash-locked like the
  other CI tools. uv is pinned to 0.12.21 in `setup-uv`; `[tool.uv] required-version` is still
  not set because the local uv here is older (see the scaffold notes).
- Not done: a Python 3.15 canary (the research note says to add 3.15 after its final release), a
  `merge_group` trigger (no merge queue is configured), and a task runner (taste).

## Scaffold notes (2026-10-05)
- Local uv is 0.8.10, so `[tool.uv] required-version` is not set (it would refuse to run). The
  build requirement `uv_build>=0.12.21,<0.13` is fetched in isolation and works with it.
- `[tool.coverage.run] source_pkgs` is deliberately absent: together with `--cov=staan_mcp` in
  `addopts` it triggers a "module-not-measured" CoverageWarning. Coverage always runs via pytest-cov.
- `pytester` is loaded through `addopts` (`-p pytester`) for the conftest gating tests.
- `tests/live/` and `tests/integration/` hold `.gitkeep` until real tests land.
- mutmut cannot run on native Windows and Docker was not running, so test strength for
  `config.py`/`errors.py` was checked with a throwaway AST mutator (143 mutants, 7 survivors, all
  equivalent: docstrings, a `Literal` annotation and `strip` character sets). Run real mutmut in CI.
- `uv run` may rebuild the editable install after `pyproject.toml` changes; set `UV_LINK_MODE=copy`
  on this Windows box to avoid hardlink warnings.
