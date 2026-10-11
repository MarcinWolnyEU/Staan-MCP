"""Live E2E over streamable HTTP (stateful + stateless) and startup-safety checks."""
import json, os, socket, subprocess, sys, time
from pathlib import Path
import anyio, httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[2]
EXE = str(Path(sys.executable).with_name("staan-mcp.exe" if os.name == "nt" else "staan-mcp"))
OUT = ROOT / ".tmp" / "e2e"
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def env(key):
    e = {k: v for k, v in os.environ.items() if not k.startswith("STAAN_")}
    e["STAAN_API_KEY"] = key
    e["PYTHONUTF8"] = "1"
    return e


def wait_port(port, proc, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


async def drive(transport, key, secrets):
    port = free_port()
    logf = open(OUT / f"{transport}.log", "w", encoding="utf-8")
    proc = subprocess.Popen([EXE, "--transport", transport, "--port", str(port)], env=env(key),
                            stdout=logf, stderr=subprocess.STDOUT, cwd=str(ROOT))
    try:
        check(f"{transport}: server listening", wait_port(port, proc))
        url = f"http://127.0.0.1:{port}/mcp"
        with anyio.fail_after(120):
            async with Client(streamable_http_client(url)) as client:
                names = sorted(t.name for t in (await client.list_tools()).tools)
                check(f"{transport}: list_tools", names == ["answer", "web_search"], str(names))
                r = await client.call_tool("web_search", {"query": "python asyncio tutorial", "market": "en-gb", "full_content": "none"})
                data = json.loads("".join(b.text for b in r.content if isinstance(b, TextContent)))
                check(f"{transport}: web_search", not r.is_error and len(data) > 0, f"{len(data)} results; title={data[0]['title']!r}")
        # protocol-level checks
        async with httpx2.AsyncClient() as http:
            r = await http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                headers={"Accept": "application/json, text/event-stream", "Host": "evil.example.com"})
            check(f"{transport}: bad Host header rejected (DNS rebinding)", r.status_code in (400, 421), str(r.status_code))
            r = await http.post(f"http://127.0.0.1:{port}/nope", json={})
            check(f"{transport}: wrong path 404", r.status_code == 404, str(r.status_code))
            r = await http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                headers={"Accept": "application/json, text/event-stream", "Origin": "https://evil.example.com"})
            check(f"{transport}: foreign Origin rejected", r.status_code in (400, 403), str(r.status_code))
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        logf.close()
    log = (OUT / f"{transport}.log").read_text(encoding="utf-8", errors="replace")
    check(f"{transport}: no key in log", not any(s in log for s in secrets))
    print("   log tail:", log.strip().splitlines()[-3:] if log.strip() else "(empty)")


def startup(args, expect_code, expect_text, key):
    p = subprocess.run([EXE, *args], env=env(key), capture_output=True, text=True, encoding="utf-8", timeout=30, cwd=str(ROOT))
    out = p.stderr + p.stdout
    check(f"startup {' '.join(args)} -> exit {expect_code}", p.returncode == expect_code and expect_text in out, f"rc={p.returncode} msg={out.strip()[:260]!r}")


async def main():
    key = (ROOT / "api key.txt").read_text().strip()
    secrets = [(ROOT / n).read_text().strip() for n in ("api key.txt", "api key 2.txt", "disabled key.txt")]
    await drive("http", key, secrets)
    await drive("stateless", key, secrets)
    startup(["--transport", "http", "--host", "0.0.0.0"], 2, "--insecure-no-auth", key)
    startup(["--transport", "stateless", "--host", "0.0.0.0", "--insecure-no-auth"], 2, "allowed-host", key)
    startup(["--transport", "http", "--host", "::"], 2, "", key)
    failed = [n for n, ok in RESULTS if not ok]
    print("FAILED:" if failed else "ALL PASSED", failed)
    return 1 if failed else 0

sys.exit(anyio.run(main))
