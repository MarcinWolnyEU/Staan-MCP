# End-to-end verification

What was checked against the real Staan API and the real `staan-mcp` process, how to repeat it,
and what turned up. Last run: 2026-10-05, version 0.1.0, Windows 11, Python 3.13.

Unit and integration tests fake the Staan API (`httpx2.MockTransport`); this page covers what they
cannot. Keys are read from the git-ignored `*key*.txt` files at runtime and are passed to the child
process through its environment only. The scripts compare the server's stderr and log files with
every key value in memory and fail if one appears. No key value is stored anywhere.

## How to repeat

Everything runs from the repository root with `uv`. About 40 live requests in total.

| Check | Command | Live calls |
|---|---|---|
| Live tests (search, filters, UTF-8, Answer 403 mapping) | `STAAN_API_KEY=... uv run pytest tests/live --run-live --no-cov` | 7 per run |
| Stdio, five key scenarios (below) | `uv run python docs/e2e/e2e_stdio.py` | 12 |
| HTTP and stateless processes, startup refusals | `uv run python docs/e2e/e2e_http.py` | 2 |
| Built wheel in an isolated `uvx` environment | `uv build && uv run python docs/e2e/e2e_wheel.py` | 1 |
| Wheel CLI | `uvx --from dist/staan_mcp-0.1.0-py3-none-any.whl staan-mcp --version` | 0 |

The scripts expect `api key.txt` (search; Answer returns 403), `api key 2.txt` (search without
extra snippets) and `disabled key.txt` in the repository root. Logs go to `.tmp/e2e/`.
`tests/integration/test_http_process.py` repeats the no-key HTTP process check on every `pytest`
run, without network.

## Results

Stdio, driven with the `mcp` client (`uv run --frozen staan-mcp` as the server):

- `tools/list` returns exactly `answer` and `web_search`.
- `web_search`, one market (`en-gb`): simple output with and without `full_content`; raw output
  (`simple_response=false`, `full_content="html"`) returns one entry per market with
  `full_content = {text, format: "html", length}`.
- Wildcard `en-*` (10 requests) returns a de-duplicated, interleaved list (94 results).
- `include_domains: ["https://www.python.org/"]` is normalised and returns only `python.org` URLs.
- `offset: 10` returns a different page from `offset: 0`.
- Invalid market and include+exclude together give a clean tool error (`is_error`), no traceback.
- `answer` with the primary key: `is_error`, text "The Answer API is not enabled for this API key
  (invitation-only, contact support@staan.ai). Web search is not affected." (403 mapping).
- Key 2 (no `extra_snippets` in its plan): simple output still works, snippets are not joined.
- Disabled key: "This Staan API key is disabled. ..." (`KEY_DISABLED`).
- No key: tools are still listed; calls fail with "STAAN_API_KEY is not set. ...". The missing key
  is a startup warning only.

HTTP (`--transport http` and `--transport stateless`, free loopback port): `tools/list` and one live
`web_search` over streamable HTTP. A foreign `Host` header gets 421, a foreign `Origin` 403, a wrong
path 404. Startup refusals exit with status 2 and a one-line message: a non-loopback host without
`--insecure-no-auth` (`0.0.0.0` and `::`), and with `--insecure-no-auth` but no `--allowed-host`.

Wheel: `--version`, `--help`, the refusal above and one live stdio search work from an isolated
environment (`uvx --from dist/<wheel>`); the wheel holds only `staan_mcp/`, `py.typed`, the
licence and metadata.

## Surprises and limitations

- The Answer API cannot be verified: the keys we have get 403, so `answer` follows the
  documentation only. The 403 mapping is the only live-verified part. A key with Answer enabled
  makes the two answer tests in `tests/live/` skip rather than fail.
- Uvicorn logged one INFO access line per request even at the default `--log-level WARNING`,
  because the SDK defaults its server log level to INFO. `main` now copies `--log-level` to
  `server.settings.log_level` before the server starts (see DESIGN.md 4.9).
- Result counts vary: `offset: 0` returned 6 to 10 results for the same query on different
  runs, so assertions use `> 0` and `<= 10`, never an exact count.
- Windows consoles with a legacy code page show `?` or a replacement glyph for characters such as
  the en dash in titles; the payload is valid UTF-8 (the live tests assert there is no `U+FFFD`).
- Running `uv run staan-mcp` as an MCP stdio command works but spawns `uv` then Python. The HTTP
  scripts start the console script from `.venv` directly, so a terminate reaches the server.
- mutmut needs `fork()`: it runs in Docker on Windows (see `docs/QUALITY.md`). Docker Desktop must
  be started first.
