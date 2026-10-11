"""One live search through the built wheel, run in an isolated uvx environment."""
import glob, json, os, sys
from pathlib import Path
import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent
ROOT = Path(__file__).resolve().parents[2]
wheel = glob.glob(str(ROOT / "dist/*.whl"))[0]
key = (ROOT / "api key.txt").read_text().strip()
env = {k: v for k, v in os.environ.items() if not k.startswith("STAAN_")} | {"STAAN_API_KEY": key}
p = StdioServerParameters(command="uvx", args=["--from", wheel, "staan-mcp"], env=env, cwd=os.environ.get("TEMP"))
async def main():
    with open(os.devnull, "w") as errlog, anyio.fail_after(180):
        async with Client(stdio_client(p, errlog=errlog)) as c:
            r = await c.call_tool("web_search", {"query": "rust ownership", "market": "en-us", "full_content": "none"})
            t = "".join(b.text for b in r.content if isinstance(b, TextContent))
            d = json.loads(t)
            ok = not r.is_error and len(d) > 0 and "\ufffd" not in t
            print("PASS" if ok else "FAIL", "wheel stdio search", len(d), "results; U+FFFD present:", "\ufffd" in t)
            return 0 if ok else 1
sys.exit(anyio.run(main))
