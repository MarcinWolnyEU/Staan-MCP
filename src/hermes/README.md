# Staan plugin for Hermes Agent

Staan web search and cited answers as native tools of [Hermes Agent](https://hermes-agent.nousresearch.com/).
This directory is a Hermes directory plugin. It is separate from the MCP server in `src/staan_mcp`
(the server never imports it, and it is not part of the `staan-mcp` wheel). Hermes can also use the
server itself over MCP; see [the main README](../../README.md#hermes-agent). Pick one: the tools
below do the same as the MCP server's, without a second process.

| Tool | Does |
|---|---|
| `staan_web_search` | Same as the MCP tool `web_search`: ranked results, optional page content, one or many markets, domain filters, paging. |
| `staan_answer` | Same as the MCP tool `answer`: a grounded answer with citations. Invitation-only on Staan's side, see the main README. |

They are called `staan_*` because Hermes already has a `web_search` tool. Parameters, limits, retries,
rate limiting and error messages are those of the standalone `stdio_server.py`: a call goes through
the same code. Errors come back as `{"error": "..."}`.

## Install

The plugin needs the `staan_mcp` package, and only its standard-library modules (no `mcp`, `httpx2`
or `pydantic`: Hermes pins its own).

**From a checkout (nothing is installed).** Clone the repository and link this directory into
Hermes' plugins directory, so that `src/staan_mcp` stays next to it:

```bash
git clone https://github.com/MarcinWolnyEU/Staan-MCP
ln -s "$PWD/Staan-MCP/src/hermes" ~/.hermes/plugins/staan     # Windows: mklink /J, or a junction
hermes plugins enable staan
```

**Installing only this directory.** `hermes plugins install MarcinWolnyEU/Staan-MCP/src/hermes`
fetches just `src/hermes`, so `staan_mcp` has to come from Hermes' own Python environment:
`pip install --no-deps staan-mcp` there (`--no-deps`, so that nothing of Hermes' pinned packages is
touched). Then `hermes plugins enable staan`.

## Configure

`STAAN_API_KEY` is required (create one in the [Staan console](https://staan.ai/console/keys));
Hermes asks for it at install and keeps it in `~/.hermes/.env`. Without it the tools are hidden from
the model. The other `STAAN_*` variables of the [configuration](../../README.md#staan-api) work as
for the server (`STAAN_BASE_URL`, `STAAN_TIMEOUT`, `STAAN_DEADLINE`, `STAAN_MAX_RETRIES`,
`STAAN_RATE_LIMIT`, `STAAN_MCP_MAX_CONTENT_CHARS`). Inside Hermes they are read per profile
(`agent.secret_scope.get_secret`), so one profile never sees another's key.

## Develop

`hermes plugins doctor src/hermes` and `hermes plugins validate src/hermes` check the manifest and
the registration. The tests are `tests/unit/test_hermes_tools.py`,
`tests/integration/test_hermes_plugin_load.py` and `tests/repo/test_hermes_manifest.py`. The version
in `plugin.yaml` is bumped with the package by `cz bump`.
