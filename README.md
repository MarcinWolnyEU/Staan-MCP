<!-- mcp-name: io.github.MarcinWolnyEU/staan-mcp -->

# staan-mcp

<!-- cacheBust on the two PyPI badges: GitHub's image proxy caches badge images per URL for 24 h and
     kept serving the pre-publish "package or version not found" render after the first PyPI release.
     The query is inert; it only gives the image proxy a fresh cache key. Keep it. -->
[![Python versions](https://img.shields.io/pypi/pyversions/staan-mcp?cacheBust=1)](https://pypi.org/project/staan-mcp/)
[![PyPI version](https://img.shields.io/pypi/v/staan-mcp?cacheBust=1)](https://pypi.org/project/staan-mcp/)
[![CI](https://github.com/MarcinWolnyEU/Staan-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/MarcinWolnyEU/Staan-MCP/actions/workflows/ci.yml)
[![Codecov](https://codecov.io/gh/MarcinWolnyEU/Staan-MCP/graph/badge.svg)](https://codecov.io/gh/MarcinWolnyEU/Staan-MCP)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/MarcinWolnyEU/Staan-MCP/badge)](https://scorecard.dev/viewer/?uri=github.com/MarcinWolnyEU/Staan-MCP)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/MarcinWolnyEU/Staan-MCP/blob/main/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue)](https://mypy-lang.org/)
[![MCP Registry](https://img.shields.io/badge/MCP%20Registry-io.github.MarcinWolnyEU%2Fstaan--mcp-informational)](https://registry.modelcontextprotocol.io/)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server for the European search engine [STAAN](https://staan.ai/) (Search Trusted API Access Network) built by the [European Perspective SAS](https://www.eu-searchperspective.com/), co-owned by Qwant and Ecosia search engines. It's an open European alternative to Exa Search and other SERP services, with its own index, oriented primarily at the European markets (+parts of Commonwealth), giving it a unique perspective that other search providers might be missing. Free subscription grants 1000 requests per month.

MCP comes with two tools:

- `web_search`: web search with optional full-page content (Markdown or HTML), in one or many Staan markets at once.
- `answer`: a grounded answer with citations, rendered as Markdown with GitHub-style footnotes.

It runs over stdio (the default), streamable HTTP, or stateless streamable HTTP, and can protect the HTTP transports with an optional OAuth 2.1 resource-server gate. If you only need stdio, it can also run [with nothing installed](#three-ways-to-run-it), just download the zip of the repository and point your AI agent to this file (README.md).

 > **Note:** `staan-mcp` is not affiliated with Staan, European Perspective, Qwant or Ecosia. It's just a solo project to deliver quality Python MCP that, at the time of release, did not exist.

> **Important:** the `answer` API endpoint is invitation-only and even if your invitation was granted - it might be tempolarly disabled at any point in time, resulting in `{"message":"Forbidden","statusCode":403}` responses.

<p align="center">
  <img src="https://raw.githubusercontent.com/MarcinWolnyEU/Staan-MCP/main/logo.png" alt="staan-mcp logo" width="600">
</p>

## Quickstart

You need a Staan API key (create one in the [Staan console](https://staan.ai/console/keys), the free plan includes 1,000 requests per month). Then if you have [uv](https://docs.astral.sh/uv/) installed you can simply configure your harness - you don't need to download anything from this repository here, uv through the uvx command will download the latest version of the MCP automatically for you.

```json
{
  "mcpServers": {
    "staan": {
      "command": "uvx",
      "args": ["staan-mcp"],
      "env": { "STAAN_API_KEY": "your-staan-api-key" }
    }
  }
}
```

This exact JSON works in Claude Desktop and Cursor. Claude Code, VS Code and Hermes Agent use a different format: see [Client configuration](#client-configuration).

If you are an LLM or simply need more details, continue further on.

## Detailed setup

### Three ways to run it

| | 1. Installed package | 2. Download and run (checkout) | 3. Single-file bundle |
|---|---|---|---|
| How | `uvx staan-mcp`, or `pip install staan-mcp` | `git clone` (or the zip of the source tree), then `python src/staan_mcp/stdio_server.py` | download `staan-mcp-stdio.pyz` from the [latest release](https://github.com/MarcinWolnyEU/Staan-MCP/releases/latest), then `python staan-mcp-stdio.pyz` |
| Python | 3.11 or newer | 3.11 or newer, plain interpreter, nothing installed | 3.11 or newer, plain interpreter, nothing installed |
| Dependencies | the MCP SDK, `httpx2`, `pydantic`, `anyio` (and `pyjwt` for the OAuth gate) | none (standard library only) | none (standard library only) |
| Transports | stdio, streamable HTTP, stateless streamable HTTP | stdio | stdio |
| OAuth gate for HTTP | yes | no | no |
| Tools, parameters, results, error messages | the reference | identical (see [below](#without-installing-anything)) | identical (the same code) |

Only the installed package serves HTTP and the OAuth gate. Ways 2 and 3 run the same
`stdio_server.py`; the bundle is that file and the few standard-library modules it imports, zipped
into one file you can put anywhere.

## Client configuration

All examples launch the server with `uvx` and pass the key through the `STAAN_API_KEY`
environment variable. Replace `your-staan-api-key` with your key.

### Claude Desktop

Edit `claude_desktop_config.json` (Settings, Developer, Edit Config):

```json
{
  "mcpServers": {
    "staan": {
      "command": "uvx",
      "args": ["staan-mcp"],
      "env": { "STAAN_API_KEY": "your-staan-api-key" }
    }
  }
}
```

### Claude Code

```bash
claude mcp add staan --env STAAN_API_KEY=your-staan-api-key -- uvx staan-mcp
```

Add `--scope user` to make it available in every project, or `--scope project` to write a
shareable `.mcp.json` (do not put the key in a file you commit; use environment variable
expansion such as `"STAAN_API_KEY": "${STAAN_API_KEY}"` there).

### VS Code

Create `.vscode/mcp.json`. VS Code prompts for the key once and stores it securely:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "staan-api-key",
      "description": "Staan API key",
      "password": true
    }
  ],
  "servers": {
    "staan": {
      "type": "stdio",
      "command": "uvx",
      "args": ["staan-mcp"],
      "env": { "STAAN_API_KEY": "${input:staan-api-key}" }
    }
  }
}
```

### Cursor

Create `.cursor/mcp.json` in your project, or `~/.cursor/mcp.json` for every project. Cursor
expands `${env:NAME}`, so the key can stay in your shell environment:

```json
{
  "mcpServers": {
    "staan": {
      "command": "uvx",
      "args": ["staan-mcp"],
      "env": { "STAAN_API_KEY": "${env:STAAN_API_KEY}" }
    }
  }
}
```

### Hermes Agent

Hermes reads MCP servers from `mcp_servers` in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  staan:
    command: "uvx"
    args: ["staan-mcp"]
    env:
      STAAN_API_KEY: "your-staan-api-key"
```

Hermes also has a native plugin that offers the same two tools as `staan_web_search` and
`staan_answer`, without a second process: see [`src/hermes`](src/hermes/README.md). It is separate
from the server and not part of the `staan-mcp` package.

### Any other client

Any client that can launch a stdio command works: run `uvx staan-mcp` with `STAAN_API_KEY` in
the environment. For HTTP clients, see [Transports](#transports).

### Without installing anything

Ways 2 and 3 need no `pip install`, no virtual environment and no third-party package: only a
plain Python 3.11 or newer, and they serve stdio only (HTTP and the OAuth gate need the
[installed package](#quickstart)).

**Single-file bundle (way 3).** Download `staan-mcp-stdio.pyz` from the
[latest release](https://github.com/MarcinWolnyEU/Staan-MCP/releases/latest), put it anywhere and
point your client at it:

```json
{
  "mcpServers": {
    "staan": {
      "command": "python",
      "args": ["/path/to/staan-mcp-stdio.pyz"],
      "env": { "STAAN_API_KEY": "your-staan-api-key" }
    }
  }
}
```

The file is a Python zip application: `python staan-mcp-stdio.pyz --version` prints
`staan-stdio X.Y.Z`. It contains exactly the standard-library modules of the package that
`stdio_server.py` imports, and nothing else. Each release attaches it next to the wheel and sdist,
with a signed build provenance attestation
(`gh attestation verify staan-mcp-stdio.pyz --repo MarcinWolnyEU/Staan-MCP`). The build is
reproducible: `python scripts/build_zipapp.py` in a checkout of the tag writes byte-identical
output and prints its SHA-256.

**From a checkout (way 2).** Download the repository (`git clone` or the zip of the source tree),
keep `src/staan_mcp/` together, and point your client at one file,
`src/staan_mcp/stdio_server.py`:

```json
{
  "mcpServers": {
    "staan": {
      "command": "python",
      "args": ["/path/to/Staan-MCP/src/staan_mcp/stdio_server.py"],
      "env": { "STAAN_API_KEY": "your-staan-api-key" }
    }
  }
}
```

The script puts its own `src` directory on `sys.path` and reuses the package's standard-library
modules (markets, domains, result formatting, errors, configuration and the tool texts), so it is
not a file you can copy on its own. Copied away from the repository it prints one line saying so
and exits with status 2, without a traceback; use the bundle instead.

**Which Python.** Both need Python 3.11 or newer. On Windows, `python` may be missing or be the
Microsoft Store stub; use the launcher with `"command": "py", "args": ["-3",
"C:\\path\\to\\staan-mcp-stdio.pyz"]` (JSON needs the doubled backslashes), or the full path of
the interpreter. On macOS and Linux the command is often `python3`. With an older Python the file
stops at once with `staan-mcp needs Python 3.11 or newer; this is X.Y.Z (path)` and status 2.
Check with `python --version`.

**What it is.** It never imports the MCP SDK, `httpx2`, `pydantic`, `anyio` or `pyjwt`; a test
runs it with `python -I -S` and with a blocker on every non-standard-library import to prove it,
and runs the built bundle the same way. It offers the same `web_search` and `answer` tools,
environment variables, results and error messages as the installed package (tests compare the two
on the same inputs), over stdio only. It has its own small `urllib` transport instead of `httpx2`,
and the same retry policy. It is checked by the same lint, type, complexity and coverage gates as
the rest of the package; only mutation testing skips it.

## Tool reference

Both tools are read-only and open-world (they reach an external service, and change nothing).
Failures are reported as MCP tool errors with a short, safe message that never contains your key.

### `web_search`

Searches the web through `POST /v2/search/web`. Each call returns one page of up to 10 results
per market.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | string, 1 to 400 chars | required | The search query. Supports `site:` and `-site:` operators. |
| `market` | string or list of strings | `"en-gb"` | One or more Staan markets. Supports wildcards, see below. |
| `full_content` | `"none"`, `"markdown"` or `"html"` | `"none"` | Return each result page's text in that format. The default `"none"` leaves it out, which keeps the response (and your token use) small. Cost and latency barely change, see the notes below. |
| `offset` | `0`, `10`, `20` or `30` | `0` | Result offset for paging. Each page is a separate billable request. |
| `include_domains` | list of strings, at most 10 | none | Restrict results to these domains. A domain also covers its subdomains. |
| `exclude_domains` | list of strings, at most 10 | none | Drop results from these domains, subdomains included. Cannot be combined with `include_domains`. |
| `simple_response` | boolean | `true` | `true` returns a flat, compact list. `false` returns the raw upstream responses. |

Notes:

- `count` is not a parameter. Staan only accepts a page size of 10, and the server always sends
  it. Use `offset` to page.
- Domains are normalised for you: `https://www.Example.com:443/a?b` becomes `example.com`, and
  `en.wikipedia.org` stays `en.wikipedia.org`.
- The server always requests extra snippets. If your key's plan includes them, they are merged
  into each result's `snippet`. If not, the field is simply absent upstream and nothing breaks.
- A listed domain also covers its subdomains. Checked live on 2026-10-05: `include_domains:
  ["python.org"]` returned `discuss.python.org`, `docs.python.org`, `wiki.python.org` and
  `www.python.org`, and `exclude_domains: ["python.org"]` removed all of them. You do not need to
  list subdomains one by one.
- **`full_content` changes the size of the response, not its cost.** The server always asks Staan
  for extra snippets, which is what makes Staan fetch and rank page text, so a `"none"` request
  is billed in the same tier and takes about as long as a `"markdown"` one (measured on
  2026-10-05: about 0.1 to 0.5 s apart). What differs is the amount of text: one `en-gb` call with
  `"markdown"` returned about 130,000 characters of page text in a single market (roughly 30,000
  to 40,000 tokens), and `"html"` is several times larger. The default `"none"` brings the same
  call down to a small fraction (about 20 KB in that test). Ask for `"markdown"` only when you
  need to read the pages.
- With `full_content: "markdown"` or `"html"` the output size is unbounded by default. Many
  clients cap a tool result (Claude Code's default is about 25,000 tokens), so such a call can be
  truncated or rejected there, and `market: "*"` multiplies it by up to 12. The operator can cap
  each page with `STAAN_MCP_MAX_CONTENT_CHARS` (see [Configuration](#configuration)): longer pages
  are cut at a paragraph boundary and end with `[... truncated: N of M characters shown ...]`.
  Snippets and extra snippets are never cut. The limit is off by default;
  `STAAN_MCP_MAX_CONTENT_CHARS=8000` is a reasonable starting point if your client limits tool
  output and you want page text.

#### Markets and wildcards

Staan supports 12 markets: `fr-fr`, `de-de`, `en-us`, `en-gb`, `en-fr`, `en-ca`, `en-au`,
`en-in`, `en-ie`, `en-nz`, `en-za` and `en-sg`. Codes are case-insensitive.

`market` accepts a single code, a list of codes, or patterns using shell-style wildcards
(`*`, `?` and `[...]`, matched against the 12 codes):

| Value | Expands to |
|---|---|
| `"en-gb"` | `en-gb` |
| `["en-us", "en-gb"]` | `en-us`, `en-gb` |
| `"en-*"` or `"en*"` | the 10 `en-` markets |
| `"*-fr"` | `fr-fr`, `en-fr` |
| `"*"` | all 12 markets |

Duplicates are removed and the first-seen order is kept. An empty value, an unknown code, or a
pattern that matches nothing is an error that lists the valid markets.

> **Warning: every market is one billable request, and so is every page.** `market: "*"` costs 12 requests
> per call, and `market: "en-*"` costs 10. Staan also enforces a rate limit of 20 requests
> per second, which the server keeps to by delaying requests (see [Rate limiting](#rate-limiting)).
> Prefer an explicit, short list of markets. Check
> [Staan's pricing](https://staan.ai) for the current rates (when this was written, the first
> 1,000 requests per month were free, plain web search was EUR 1 per 1,000 requests, and
> requests with extra snippets or full content were EUR 2 per 1,000).

With several markets the requests run concurrently. If some markets fail, the others are still
returned and a warning naming the failed markets is logged on stderr. The tool only fails when
every market fails, or when the key itself is rejected (invalid, disabled, or not permitted),
because those errors would repeat for every market.

#### Output with `simple_response=true` (default)

A single JSON array, as text content. With several markets, results are interleaved round-robin
by rank (rank 0 of the first market, rank 0 of the second, ..., then rank 1, and so on) and
de-duplicated by URL, so the first occurrence wins.

```json
[
  {
    "title": "Top 7 open source LLMs for 2026",
    "url": "https://www.example.com/open-source-llms-2026",
    "snippet": "Top open source LLMs in 2026 (...) Open source LLMs are fully accessible for anyone to use, modify and distribute.",
    "full_content": "# Top 7 open source LLMs for 2026\n\nLarge Language Models are ..."
  },
  {
    "title": "A list of open LLMs available for commercial use",
    "url": "https://github.com/example/open-llms",
    "snippet": "A list of open LLMs available for commercial use."
  }
]
```

- `snippet` is the upstream snippet, followed by any extra snippets joined with ` (...) `.
- `full_content` appears only when you asked for it and Staan returned non-empty text. A page
  that could not be fetched has no `full_content` key. The example shows the first result with
  `full_content: "markdown"`; by default no result has the key.

#### Output with `simple_response=false`

A JSON array with one entry per requested market, in market order. A successful market is the
raw upstream response (abridged here). A failed market is an object with the market and a message.

```json
[
  {
    "search_id": "0d920ee2fc05ffd5fc834ba3ed346b02",
    "query": { "q": "open source llms", "market": "en-gb", "count": 10, "offset": 0 },
    "web": {
      "results": [
        {
          "title": "Top 7 open source LLMs for 2026",
          "url": "https://www.example.com/open-source-llms-2026",
          "snippet": "Top open source LLMs in 2026 ...",
          "display_url": "https://www.example.com/open-source-llms-2026",
          "hostname": "www.example.com",
          "extra_snippets": [{ "chunk": "Open source LLMs are ...", "score": 0.99 }],
          "full_content": { "text": "# Top 7 ...", "format": "markdown", "length": 5120 }
        }
      ]
    }
  },
  { "market": "de-de", "error": "Staan rate limit reached (HTTP 429). Try again shortly." }
]
```

Example tool calls:

```json
{ "query": "python 3.14 release notes", "full_content": "markdown" }
{ "query": "bundesliga tabelle", "market": "de-de", "include_domains": ["kicker.de"] }
{ "query": "oat milk prices", "market": ["en-gb", "en-ie"], "offset": 10, "simple_response": false }
```

### `answer`

Asks `POST /v2/answer` for an answer grounded in web sources (or in sources you supply). The
server always asks for a non-streamed, Markdown answer, and never sends conversation history.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | string, 1 to 2,000 chars | required | The question. |
| `filter` | string | none | Limit search to one domain, for example `frandroid.com`. URLs are accepted and reduced to the bare domain. An empty string means no filter. Ignored by Staan when `sources` is set. |
| `language` | `"en"` or `"fr"` | `"en"` | Language of the answer. (Staan's own default is French.) |
| `mode` | `"short"` or `"long"` | `"short"` | Length of the answer. |
| `sources` | list of source objects, at most 10 | none | Ground the answer in your own sources instead of a web search. |
| `sources_mode` | `"search"` or `"context"` | `"search"` | `"search"` fetches and re-ranks the sources, `"context"` injects their `content` directly. Only sent when `sources` is set. |
| `simple_response` | boolean | `true` | `true` returns just the answer text with citations. `false` returns the raw upstream object. |

A source object has optional `url`, `title` (up to 500 chars) and `content` (up to 50,000
chars). Each source needs at least a `url` or `content`.

Staan fetches the `url` values you pass, so they are validated first: an absolute `http` or
`https` URL with a host, at most 2,048 characters, no whitespace or control characters, and no
literal private, loopback, link-local or reserved IP address (nor `localhost`). Anything else is
rejected before a request is sent. This is a filter on the literal URL, not a guarantee about what
a host name resolves to.

#### Output with `simple_response=true` (default)

An object with one key. Citations become GitHub-flavoured Markdown footnotes: a marker such as
`[^1]` is inserted at each cited position, and a footnote list with the source URLs is
appended. Footnotes are numbered in order of first appearance, and only cited sources are listed.

```json
{
  "answer": "The Eiffel Tower is about 330 metres tall.[^1] It was completed in 1889 for the World Fair.[^2] Millions of people visit it every year.\n\n[^1]: https://example.org/eiffel-tower\n[^2]: https://example.org/world-fair-1889"
}
```

If the response has no usable citations, the answer is returned unchanged. A response without a
text `answer` field is reported as a tool error (with either value of `simple_response`).

Details of the footnote rendering:

- A citation is matched to its source by `source_id`. Sources that are numbered rather than named
  (no ids at all) are matched by position (`"2"` is the second source). When the sources do have
  ids, an unknown id is dropped instead of being guessed, because a wrong citation is worse than
  none.
- A marker never lands inside a code fence or a table, or at the end of one, since that would
  break the block and swallow the footnote list. It moves to the end of the text before the block
  (a citation for a block at the very start of the answer is dropped).
- Only `http` and `https` source URLs without whitespace or control characters, and of at most
  2,048 characters, are listed. Others are ignored, so upstream text cannot inject Markdown after
  the footnotes.

The tool declares its output schema: an object with a required `answer` string (plus the other
upstream fields when `simple_response` is `false`). The text content is the same JSON, compact.

#### Output with `simple_response=false`

The raw upstream object, as documented by Staan (abridged):

```json
{
  "request_id": "01938fc2-1b3a-7e4d-9c12-a4f8e3c20d91",
  "answer": "The Eiffel Tower is about 330 metres tall. It was completed in 1889 ...",
  "citations": [{ "position": 42, "source_id": "1" }, { "position": 87, "source_id": "3" }],
  "sources": [
    { "id": "1", "url": "https://example.org/eiffel-tower", "title": "Eiffel Tower facts" },
    { "id": "3", "url": "https://example.org/world-fair-1889", "title": "The 1889 World Fair" }
  ],
  "related_queries": ["How was the Eiffel Tower built?"],
  "usages": [{ "step": "answer", "input_tokens": 2840, "output_tokens": 194 }]
}
```

Example tool calls:

```json
{ "query": "What changed in the latest EU AI Act guidance?", "mode": "long" }
{ "query": "meilleur smartphone 2026", "language": "fr", "filter": "frandroid.com" }
{
  "query": "Summarise our release policy",
  "sources_mode": "context",
  "sources": [{ "title": "Policy", "content": "Releases happen on the first Monday of the month ..." }]
}
```

### Errors

| Situation | What the tool reports |
|---|---|
| `STAAN_API_KEY` not set | A configuration error naming the variable. |
| Key rejected (401) | An authentication error. Check the key. |
| Key disabled (403, `KEY_DISABLED`) | A disabled-key error. |
| Not permitted (403) | For `answer`: not enabled for this API key (invitation-only, contact `support@staan.ai`), or temporarily disabled by Staan. |
| Invalid arguments (400, 422) | Staan's validation messages, joined with `; `. |
| Rate limited (429), server errors (5xx), connection failures | The request is retried (see `STAAN_MAX_RETRIES`), then a rate-limit, server or transport error is reported. Server error text from upstream is never echoed. |
| Read timeout (no answer within `STAAN_TIMEOUT`) | Reported at once and **not retried**: the request had already been sent, so a retry would be billed twice. |
| Request would have to wait past `STAAN_DEADLINE` for the [rate limit](#rate-limiting) | The same deadline error, at once, without sending that request. |
| Call takes longer than `STAAN_DEADLINE` | A transport error after 50 s by default, retries and waits included, so the model gets a clean error before a typical 60 s client timeout. |
| `STAAN_BASE_URL` or `STAAN_API_KEY` unusable for an HTTP request | A configuration error naming both variables (and never the key). It ends the whole call at once, whatever the number of markets. |
| A response larger than 32 MiB | A response error; the body is discarded. |

### Rate limiting

Staan caps an API key at 20 requests per second. `market: "*"` sends 12 requests at once, so two
such calls at the same time (24 requests) would exceed it, and every retry is a request too. The
server therefore counts the requests it sends and **delays** the ones that would not fit; it
never refuses them, and a delayed call just takes a little longer.

- **What counts.** Every request to Staan that one server sends: `web_search` and `answer`, every
  market of a call, every retry. At most `STAAN_RATE_LIMIT` requests (20 by default) start in
  any window of one second (a sliding window, so there is no burst of 40 across two
  seconds), plus a safety margin of 50 ms per window that absorbs timing jitter: at the default,
  20 requests per 1.05 s. Requests are served in the order they were made.
- **Example.** Two simultaneous `market: "*"` calls: 20 of the 24 requests start at once, the
  other four 1.05 s later. Both calls return complete results.
- **Setting it.** `STAAN_RATE_LIMIT` is requests per second. From 1 upwards a fraction is
  rounded down (`2.5` allows 2 a second); below 1 requests are spaced `1 / rate` seconds apart
  (`0.5` is one every two seconds). `0` turns the limiter off. Values above 20 are accepted
  (your plan may allow more), but 20 is Staan's default cap, so raise it only if you know yours is
  higher.
- **Deadline.** The wait is part of the call, so it counts towards `STAAN_DEADLINE` (and not
  towards `STAAN_TIMEOUT`, which times one request on the wire). A request that would have to wait
  past the deadline fails at once with the deadline error and is not sent. A call that is cancelled
  while waiting never sends its pending requests, and the slots they had are free for others.
- **Scope.** The budget belongs to one server process. Several server processes that share
  one API key (two clients, a Docker container next to a local one) do not know about each other,
  so their budgets add up: split the budget between them with `STAAN_RATE_LIMIT`.
- **Logs.** A delayed request is logged at `DEBUG` level with its delay (the download-and-run
  variants write one line to stderr); requests within the budget log nothing.

## Transports

| Transport | Flag | Description |
|---|---|---|
| stdio | `--transport stdio` (default) | The client launches the server as a subprocess. Credentials come from the environment. Logs go to stderr. |
| http | `--transport http` | [Streamable HTTP](https://modelcontextprotocol.io/specification/latest/basic/transports#streamable-http) with sessions, served at `http://HOST:PORT/mcp`. |
| stateless | `--transport stateless` | Streamable HTTP without sessions and with plain JSON responses (no SSE). Each request is independent, so it works behind a load balancer and on serverless platforms. |

The download-and-run variants ([ways 2 and 3](#three-ways-to-run-it)) serve stdio only; for HTTP
or the OAuth gate, install the package.

```bash
# Local HTTP server on 127.0.0.1:8000/mcp (loopback only, safe by default)
STAAN_API_KEY=... uvx staan-mcp --transport http

# Connect a client
claude mcp add --transport http staan http://127.0.0.1:8000/mcp
```

HTTP startup rules (a violation prints a message to stderr and exits with status 2):

- On a loopback host (`localhost` in any case, any `127.0.0.0/8` address, `::1` in any
  spelling) no extra options are needed. DNS-rebinding protection is always active there: the
  server hands the SDK explicit settings that accept only loopback `Host` and `Origin` headers and
  answer anything else with `421` or `403`. (The SDK switches its own protection on for just the
  three exact strings `127.0.0.1`, `localhost` and `::1`, so other spellings would otherwise have
  been left open.) The bind address is also trimmed, lowercased and unbracketed (`[::1]` becomes
  `::1`).
- A **non-loopback** host requires access control: either the OAuth gate
  ([below](#oauth-gate)), or the explicit `--insecure-no-auth` flag.
- A non-loopback host also requires at least one `--allowed-host` (the `Host` header values
  you expect, for example `mcp.example.com` and `mcp.example.com:*`).
- The OAuth gate is not available on stdio. Configuring it there is rejected, because stdio
  clients should use environment credentials.

## Configuration

Every option can be set with a flag or an environment variable. Flags win over the environment.
Blank environment values count as unset.

The download-and-run variants read the [Staan API](#staan-api) variables below (they use no
others) and accept only `--version` and `--help`: the server and OAuth tables apply to the
installed package.

### Staan API

| Variable | Default | Description |
|---|---|---|
| `STAAN_API_KEY` | none | Staan API key, sent upstream as `Authorization: Bearer`. Required to call a tool. Must be visible ASCII without spaces (a key with a pasted smart quote is refused at startup, without echoing it). |
| `STAAN_BASE_URL` | `https://api.staan.ai/v2` | API base URL. Must be https unless the host is loopback, must have a valid port, and must not contain credentials (`user:password@`). |
| `STAAN_TIMEOUT` | `30` | Timeout of one attempt, in seconds (must be greater than 0). |
| `STAAN_DEADLINE` | `50` | Overall limit for one tool call, retries and waits included, in seconds (must be greater than 0). Keep it below your client's own tool timeout (often 60 s). |
| `STAAN_MAX_RETRIES` | `2` | Retries for 429, 500, 502, 503, 504 and connection errors (0 to 10); read and write timeouts are never retried. Backoff is `min(0.5 * 2**attempt, 8)` seconds, plus `Retry-After` (up to 30 s) when present. |
| `STAAN_RATE_LIMIT` | `20` | Most requests per second the server starts, retries included, all tools and markets together; later ones wait (see [Rate limiting](#rate-limiting)). A number: from 1 upwards it is rounded down, below 1 it spaces requests `1 / rate` seconds apart. `0` turns the limit off. Staan's default cap is 20. |
| `STAAN_MCP_MAX_CONTENT_CHARS` | `0` (no limit) | Longest `full_content` text kept per search result (`0`, or at least 200). Longer pages are cut at a paragraph boundary and marked `[... truncated ...]`. Applies to both `simple_response` modes. |

### Server

| Flag | Environment variable | Default | Description |
|---|---|---|---|
| `--transport {stdio,http,stateless}` | `STAAN_MCP_TRANSPORT` | `stdio` | Transport to serve. |
| `--host HOST` | `STAAN_MCP_HOST` | `127.0.0.1` | Bind address (HTTP transports). |
| `--port PORT` | `STAAN_MCP_PORT` | `8000` | Bind port (HTTP transports). |
| `--path PATH` | `STAAN_MCP_PATH` | `/mcp` | URL path of the MCP endpoint. |
| `--allowed-host HOST` | `STAAN_MCP_ALLOWED_HOSTS` | none | Accepted `Host` header value. Repeat the flag, or comma-separate the variable. |
| `--allowed-origin ORIGIN` | `STAAN_MCP_ALLOWED_ORIGINS` | none | Accepted `Origin` header value. Same syntax. |
| `--insecure-no-auth` | none | off | Allow a non-loopback HTTP bind without the OAuth gate. Use only behind a trusted, authenticated proxy. |
| `--log-level LEVEL` | `STAAN_MCP_LOG_LEVEL` | `WARNING` | Log level, also applied to the HTTP server (`WARNING` hides its per-request access log). Logs go to stderr only. |
| `--version` | none | none | Print the version and exit. |

### OAuth gate

Setting any `STAAN_MCP_AUTH_*` variable enables the gate (HTTP transports only). Partial or
inconsistent configuration is a startup error.

| Variable | Required | Description |
|---|---|---|
| `STAAN_MCP_AUTH_ISSUER_URL` | yes | Issuer URL of your authorization server. Tokens must carry this `iss`. |
| `STAAN_MCP_AUTH_RESOURCE_URL` | yes | Canonical public URL of this MCP endpoint, for example `https://mcp.example.com/mcp`. Tokens must have this value as their audience. |
| `STAAN_MCP_AUTH_SCOPES` | no | Scopes every token must have, space- or comma-separated. |
| `STAAN_MCP_AUTH_JWKS_URL` | one of | JWKS URL for verifying JWT access tokens. Requires the `oauth` extra. |
| `STAAN_MCP_AUTH_INTROSPECTION_URL` | one of | RFC 7662 introspection endpoint. Needs a client id and secret. |
| `STAAN_MCP_AUTH_CLIENT_ID` | with introspection | Client id for introspection (HTTP Basic). |
| `STAAN_MCP_AUTH_CLIENT_SECRET` | with introspection | Client secret for introspection. |
| `STAAN_MCP_AUTH_ALGORITHMS` | no | Accepted JWT algorithms. Default `RS256,ES256`. `none` is always refused. |

All URLs must be https unless the host is loopback, and none may contain credentials
(`user:password@`). Exactly one of the JWKS and introspection URLs must be set.

An origin-only URL (no path, such as `https://auth.example.com`) is published in the
protected-resource metadata with a trailing slash, because the SDK normalises it. The checks
therefore accept both spellings of an origin-only issuer and resource: a token whose `aud` or
`iss` is `https://host` or `https://host/` passes. A URL with a path (`https://host/mcp`) is
matched exactly.

JWT verification fetches the JWKS with a 10 s timeout and refreshes it at most once per 30 s, so
tokens with an unknown `kid` cannot make the server hammer your identity provider. That needs
PyJWT 2.14 or newer, which the `oauth` extra requires. Introspection sends one credentialed
request per token to your authorization server over one shared connection pool. Tokens that are
longer than 4,096 characters or not visible ASCII are rejected locally without a request.

## Docker

The image is built from `Dockerfile` (multi-stage, uv, non-root user, includes the `oauth`
extra, based on `python:3.13-slim-trixie`). Releases publish it to GHCR for `linux/amd64` and
`linux/arm64`; a release candidate gets only its own version tag, never `latest`. You can always
build it yourself:

```bash
docker build -t staan-mcp .
```

The default command is `staan-mcp --transport stateless --host 0.0.0.0`. Because that binds a
non-loopback address, the server's startup rules apply, and **a plain `docker run staan-mcp`
refuses to start** (exit status 2, with an explanation on stderr). That is the intended safe
default: the image never exposes your key's quota unless you say how access is controlled.

stdio (for MCP clients that launch containers). Override the arguments:

```bash
docker run -i --rm -e STAAN_API_KEY staan-mcp --transport stdio
```

Claude Desktop style configuration:

```json
{
  "mcpServers": {
    "staan": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "STAAN_API_KEY", "staan-mcp", "--transport", "stdio"],
      "env": { "STAAN_API_KEY": "your-staan-api-key" }
    }
  }
}
```

HTTP on your own machine only, no OAuth. The container binds `0.0.0.0` internally, but the
published port is reachable from the host's loopback interface only:

```bash
docker run --rm -p 127.0.0.1:8000:8000 -e STAAN_API_KEY \
  staan-mcp --transport stateless --host 0.0.0.0 \
  --allowed-host localhost:8000 --allowed-host 127.0.0.1:8000 --insecure-no-auth
```

HTTP for others, with the OAuth gate. Configure everything through the environment:

```bash
docker run -d --name staan-mcp -p 8000:8000 \
  -e STAAN_API_KEY \
  -e STAAN_MCP_ALLOWED_HOSTS="mcp.example.com,mcp.example.com:*" \
  -e STAAN_MCP_AUTH_ISSUER_URL="https://auth.example.com/" \
  -e STAAN_MCP_AUTH_RESOURCE_URL="https://mcp.example.com/mcp" \
  -e STAAN_MCP_AUTH_JWKS_URL="https://auth.example.com/.well-known/jwks.json" \
  -e STAAN_MCP_AUTH_SCOPES="staan:use" \
  staan-mcp
```

Put a TLS-terminating reverse proxy in front: the server itself speaks plain HTTP. Pass
secrets with `--env-file`, Docker or Kubernetes secrets, not on the command line. The image has a
`HEALTHCHECK` that checks the TCP port, which is only meaningful for the HTTP transports (use
`--no-healthcheck` with stdio). It reads the port from `STAAN_MCP_PORT`, so change the port with
`-e STAAN_MCP_PORT=9000` rather than `--port 9000`, or the container is reported unhealthy.

The application code and its packages in the image are owned by root and read-only for the
unprivileged `app` user (uid 10001), so a code-execution bug cannot rewrite them. The server
needs no writable path, so you can lock the container down further:

```bash
docker run --rm --read-only --cap-drop=ALL --security-opt no-new-privileges \
  --tmpfs /tmp -e STAAN_API_KEY \
  staan-mcp --transport stdio
```

## Security: OAuth gate and threat model

### Credentials

- Upstream: Staan has no OAuth. The only upstream credential is the API key in
  `STAAN_API_KEY`, held by the server and sent as `Authorization: Bearer`. It is never logged,
  never included in error messages, and not shown in `repr()` of the settings object.
- Inbound: the server never forwards an inbound access token to Staan (the MCP
  specification's "token passthrough" ban). Tokens are used only to decide who may use the server.
  There is no per-request client key header and no static shared bearer token.

### OAuth gate

When configured, the HTTP transports act as an OAuth 2.1 resource server, as described in
the MCP authorization specification. You run the authorization server (any server that can issue
JWTs or answer RFC 7662 introspection). The gate:

- publishes RFC 9728 protected-resource metadata, and answers unauthenticated requests with
  `401` and a `WWW-Authenticate` header that points to it;
- verifies each token: signature (JWKS) or `active` (introspection), `exp`, issuer, **audience
  equal to `STAAN_MCP_AUTH_RESOURCE_URL`**, and the required scopes;
- accepts only the configured algorithms and never `none`;
- treats any verification failure as an invalid token, and logs the reason at debug level
  without the token.

### Threat model

| Threat | Mitigation | Residual risk |
|---|---|---|
| Someone else spends your Staan quota through an exposed HTTP port | Loopback bind by default. A non-loopback bind needs the OAuth gate or an explicit `--insecure-no-auth`, plus `--allowed-host`. | `--insecure-no-auth` is your call: put authentication in front of it. |
| DNS rebinding or a malicious web page calling a local server | `Host` and `Origin` validation (SDK protection on loopback, your allow-lists otherwise). | A wrongly set wildcard allow-list weakens this. |
| A token issued for another service is replayed here | Audience must equal the canonical resource URL. Issuer, expiry and scopes are checked. | Depends on your authorization server issuing correct audiences. |
| Stolen access token | Short-lived tokens, scope checks, TLS at your proxy. | A valid token is a valid user until it expires. Revoke at the authorization server. |
| Key leakage through logs or errors | Logs go to stderr only. Keys, `Authorization` headers and tokens are never logged or echoed. Upstream 5xx text is never relayed. | Your own client or proxy may log arguments or headers. |
| Prompt injection through search results (`full_content`, snippets) | Both tools are read-only and side-effect free: they cannot write files, run code or call other services. | Returned page text is untrusted third-party content. Your MCP client and model must treat it that way, and should not give other powerful tools to an agent that reads untrusted pages without review. |
| Cost amplification (`market: "*"`, many pages, retries) | Limits on markets and domains, a retry cap, no retry of a request that was already sent (read timeout), an overall call deadline, and a documented billing note. | One call can still cost up to 12 billable requests. Use Staan's usage controls. |
| A prompt-injected model makes Staan fetch `file://`, internal or oversized URLs through `answer` sources | Source URLs must be public-looking absolute http(s) URLs of at most 2,048 characters, with no literal private, loopback or link-local IP. | The check is on the literal URL: a host name that resolves to an internal address is not caught here. Staan does the fetching. |
| Anonymous callers make the server hammer the identity provider (forged JWTs, garbage bearer tokens) | JWKS refresh cooldown (PyJWT 2.14+), 10 s timeouts, one shared introspection connection pool, and local rejection of implausible tokens. | Per-request introspection is still one request to your authorization server for each plausible unknown token; rate-limit at your proxy. |
| Plain-text traffic on the wire | All upstream traffic uses https (an `http` base URL is refused unless loopback), and so are the OAuth URLs. | The server's own HTTP listener is plain HTTP: terminate TLS in front of it. |

Out of scope: running the authorization server, per-user quotas, and multi-tenant key
isolation. To report a vulnerability, use a [private security advisory](https://github.com/MarcinWolnyEU/Staan-MCP/security/advisories/new), not a public issue.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `STAAN_API_KEY is not set` from a tool | Add the key to the client's `env` block, or export it before starting the server. |
| Authentication error | The key is wrong or revoked. Create a new one in the Staan console. |
| `answer` says it is not enabled for this API key | The Answer API is invitation-only, and Staan may also disable it temporarily. Contact `support@staan.ai` if it persists. |
| `market` error listing valid markets | Use one of the 12 codes or a wildcard such as `en-*`. |
| Server exits with status 2 at startup | Read the message on stderr: usually a non-loopback host without OAuth or `--allowed-host`. |
| `staan-mcp: cannot import the staan_mcp package` (one line, status 2) | `stdio_server.py` was copied away from the repository. Keep it in `src/staan_mcp/` next to the other modules, or use the single `staan-mcp-stdio.pyz` bundle, or the installed package. |
| `staan-mcp needs Python 3.11 or newer; this is ...` (status 2) | The client's `command` runs an older Python. Point it at a 3.11+ interpreter, by full path if needed. |
| The client cannot start `python` | On Windows use `"command": "py", "args": ["-3", "C:\\path\\to\\staan-mcp-stdio.pyz"]` (or the interpreter's full path); on macOS and Linux the command is often `python3`. |
| HTTP client gets `421 Misdirected Request` | The `Host` header is not allowed. Add it with `--allowed-host`. |
| A tool result is truncated or rejected by the client | You asked for `full_content: "markdown"` or `"html"`, which is large (tens of thousands of tokens per call, more with several markets). Use the default `"none"`, ask for fewer markets, or set `STAAN_MCP_MAX_CONTENT_CHARS`. |
| Slow responses | Several markets run in parallel, so one call is as slow as the slowest. `full_content` adds only a little latency; the default `"none"` mainly makes the answer smaller. A call is cut off after `STAAN_DEADLINE` seconds. More than 20 requests in a second (two `market: "*"` calls) are delayed by the [rate limit](#rate-limiting). |
| `The request to Staan could not be built` | `STAAN_BASE_URL` has a bad port, or `STAAN_API_KEY` contains characters that cannot be sent in a header. Fix the variable named in the message. |
| Nothing appears in the client | Run the server in a terminal and read stderr: `uvx staan-mcp --log-level DEBUG`, or `python staan-mcp-stdio.pyz` (the download-and-run variants take no flags but log problems to stderr). Never write to stdout in stdio mode. |

## Development

You need [uv](https://docs.astral.sh/uv/). It installs the right Python and every tool.

```bash
git clone https://github.com/MarcinWolnyEU/Staan-MCP.git
cd Staan-MCP
uv sync                          # creates .venv with all dependency groups (use UV_LINK_MODE=copy on Windows across drives)
uv run staan-mcp --version
```

The quality gates, all required in CI:

```bash
uv run --frozen ruff check .                  # every rule enabled, documented opt-outs only
uv run --frozen ruff format --check .
uv run --frozen mypy                          # --strict
uv run --frozen pylint src tests scripts      # 10.00/10
uv run --frozen xenon --max-absolute B --max-modules A --max-average A src
uv run --frozen pytest                        # 100% line and branch coverage required
uv build && uv run --frozen --only-group dist twine check --strict dist/* && uv run --frozen --only-group dist check-wheel-contents dist/*.whl
uv run python scripts/build_zipapp.py         # builds dist-bundle/staan-mcp-stdio.pyz; tests/repo run it
```

- Mutation testing uses [mutmut](https://github.com/boxed/mutmut) and needs Linux, macOS or
  WSL (it does not run on native Windows; use Docker there).
  `uv run mutmut run`, then `uv run mutmut export-cicd-stats`. CI gates on
  `mutants/mutmut-cicd-stats.json`. `stdio_server.py` is the only module it skips.
- Live tests call the real Staan API. They are skipped unless `STAAN_API_KEY` is set and you
  pass `--run-live`: `uv run pytest tests/live --run-live --no-cov`. CI never runs them.
- Hooks: `uv tool install prek && prek install` (or use `pre-commit`) runs ruff, mypy,
  pylint, xenon, zizmor, actionlint, typos, schema checks for `server.json`, the workflows and
  Dependabot, secret scanning (`detect-private-key` and
  [gitleaks](https://github.com/gitleaks/gitleaks)) and a Conventional Commits check. CI runs
  the same hooks on every file (and checks the pull request title), and scans the whole git
  history with gitleaks as well. Keep key files out of the repository directory
  if you can; `.gitignore` also covers `*key*.txt`, `*.pem`, `*.key` and `credentials*`.
- Research notes on the Staan API, the MCP SDK and OAuth are in
  [`docs/research/`](https://github.com/MarcinWolnyEU/Staan-MCP/tree/main/docs/research). See the
  [changelog](https://github.com/MarcinWolnyEU/Staan-MCP/blob/main/CHANGELOG.md) for what changed.

## License

[MIT](https://github.com/MarcinWolnyEU/Staan-MCP/blob/main/LICENSE) (c) 2026 Marcin Wolny.
