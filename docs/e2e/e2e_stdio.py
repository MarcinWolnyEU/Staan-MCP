"""Live E2E over stdio. Keys are read at runtime and passed only via the child env."""
import json, os, sys
from pathlib import Path
import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[2]
CALLS = 0
OUT = ROOT / ".tmp" / "e2e"
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []


def key(name):
    return (ROOT / name).read_text(encoding="utf-8").strip()


def params(api_key):
    env = {k: v for k, v in os.environ.items() if not k.startswith("STAAN_")}
    if api_key is not None:
        env["STAAN_API_KEY"] = api_key
    return StdioServerParameters(command="uv", args=["run", "--frozen", "staan-mcp"], cwd=str(ROOT), env=env)


def text(result):
    return "".join(b.text for b in result.content if isinstance(b, TextContent))


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


async def call(client, tool, args):
    global CALLS
    CALLS += 1
    return await client.call_tool(tool, args)


async def main():
    secrets = [key(n) for n in ("api key.txt", "api key 2.txt", "disabled key.txt")]
    errlog = open(OUT / "stderr.log", "w", encoding="utf-8")

    # --- main key
    with anyio.fail_after(240):
        async with Client(stdio_client(params(secrets[0]), errlog=errlog)) as client:
            tools = (await client.list_tools()).tools
            names = sorted(t.name for t in tools)
            check("list_tools", names == ["answer", "web_search"], str(names))

            r = await call(client, "web_search", {"query": "python asyncio tutorial", "market": "en-gb", "full_content": "none"})
            data = json.loads(text(r))
            check("search simple (no full_content)", not r.is_error and isinstance(data, list) and len(data) > 0
                  and all({"title", "url", "snippet"} <= set(x) and "full_content" not in x for x in data), f"{len(data)} results")
            print("   sample:", json.dumps(data[0], ensure_ascii=False)[:300])
            check("snippet has extra snippets joined", any(" (...) " in x["snippet"] for x in data))

            r = await call(client, "web_search", {"query": "python asyncio tutorial", "market": "en-gb"})
            data = json.loads(text(r))
            check("search simple + markdown full_content", not r.is_error and any(x.get("full_content") for x in data), f"{len(data)} results")

            r = await call(client, "web_search", {"query": "python asyncio tutorial", "market": "en-gb", "full_content": "html", "simple_response": False})
            data = json.loads(text(r))
            check("search full (raw) html", not r.is_error and isinstance(data, list) and len(data) == 1 and "web" in data[0], str(list(data[0])[:5]))
            fc = data[0]["web"]["results"][0].get("full_content")
            check("raw full_content shape", isinstance(fc, dict) and fc.get("format") == "html", str(fc.get("format") if isinstance(fc, dict) else fc))

            r = await call(client, "web_search", {"query": "weather forecast", "market": "en-*", "full_content": "none"})
            data = json.loads(text(r))
            urls = [x["url"] for x in data]
            check("wildcard en-* simple deduped", not r.is_error and len(data) > 10 and len(urls) == len(set(urls)), f"{len(data)} results (10 markets)")

            r = await call(client, "web_search", {"query": "python", "market": "en-us", "full_content": "none", "include_domains": ["https://www.python.org/"]})
            data = json.loads(text(r))
            check("include_domains python.org", not r.is_error and len(data) > 0 and all("python.org" in x["url"] for x in data), f"{len(data)} results")

            r = await call(client, "web_search", {"query": "python", "market": "en-us", "full_content": "none", "offset": 10})
            data10 = json.loads(text(r))
            r0 = await call(client, "web_search", {"query": "python", "market": "en-us", "full_content": "none", "offset": 0})
            data0 = json.loads(text(r0))
            check("offset 10 differs from offset 0", not r.is_error and len(data10) > 0 and {x["url"] for x in data10} != {x["url"] for x in data0}, f"{len(data10)} vs {len(data0)}")

            r = await call(client, "web_search", {"query": "python", "market": "xx-yy"})
            check("invalid market -> clean tool error", r.is_error and "market" in text(r).lower(), text(r)[:160])
            r = await call(client, "web_search", {"query": "python", "include_domains": ["a.com"], "exclude_domains": ["b.com"]})
            check("include+exclude -> clean tool error", r.is_error, text(r)[:160])

            r = await call(client, "answer", {"query": "What is the capital of France?"})
            t = text(r)
            check("answer -> clean ToolError (invitation-only)", r.is_error and "invitation-only" in t and "support@staan.ai" in t, t[:200])

    # --- key 2
    with anyio.fail_after(120):
        async with Client(stdio_client(params(secrets[1]), errlog=errlog)) as client:
            r = await call(client, "web_search", {"query": "python asyncio tutorial", "market": "en-gb", "full_content": "none"})
            data = json.loads(text(r))
            check("key2: simple output works w/o extra snippets", not r.is_error and len(data) > 0
                  and all(x["snippet"] and " (...) " not in x["snippet"] for x in data), f"{len(data)} results")

    # --- disabled
    with anyio.fail_after(120):
        async with Client(stdio_client(params(secrets[2]), errlog=errlog)) as client:
            r = await call(client, "web_search", {"query": "python", "full_content": "none"})
            t = text(r)
            check("disabled key -> KEY_DISABLED error", r.is_error and "disabled" in t.lower(), t[:200])

    # --- no key
    with anyio.fail_after(120):
        async with Client(stdio_client(params(None), errlog=errlog)) as client:
            names = sorted(t.name for t in (await client.list_tools()).tools)
            check("no key: tools still listed", names == ["answer", "web_search"])
            r = await client.call_tool("web_search", {"query": "python"})
            t = text(r)
            check("no key -> missing-key error", r.is_error and "STAAN_API_KEY is not set" in t, t[:200])
            r = await client.call_tool("answer", {"query": "python"})
            check("no key answer -> missing-key error", r.is_error and "STAAN_API_KEY is not set" in text(r))

    errlog.close()
    err = (OUT / "stderr.log").read_text(encoding="utf-8")
    leaked = [i for i, s in enumerate(secrets) if s in err]
    check("no key value in server stderr", not leaked)
    print("live API calls made:", CALLS)
    failed = [n for n, ok in RESULTS if not ok]
    print("FAILED:" if failed else "ALL PASSED", failed)
    return 1 if failed else 0

sys.exit(anyio.run(main))
