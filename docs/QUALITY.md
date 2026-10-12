# Quality gates

Every gate below must pass before a change is merged. CI runs the same commands (see
`.github/workflows/`). Use `uv` for everything; never the system Python. On Windows, run the
commands from Git Bash or PowerShell in the repository root.

## The gates

| Gate | Command | Passes when |
|---|---|---|
| Lockfile | `uv lock --check` | `uv.lock` matches `pyproject.toml`. |
| Lint | `uv run --frozen ruff check .` | No findings (`select = ["ALL"]`, documented opt-outs only). |
| Format | `uv run --frozen ruff format --check .` | Nothing would be reformatted. |
| Types | `uv run --frozen mypy` | `--strict`, `src`, `tests` and `scripts`, no errors. |
| Pylint | `uv run --frozen pylint src tests scripts` | 10.00/10. Per-line disables need a reason. |
| Complexity | `uv run --frozen xenon --max-absolute B --max-modules A --max-average A src` | Exit status 0. |
| Metrics | `uv run --frozen radon cc -s -a src` and `uv run --frozen radon mi -s src` | Informational, not gated: every block is A or B, and every module is MI grade A except `stdio_server.py` (B, about 15), the single-file standalone server, which holds a whole JSON-RPC server, an HTTP client and a rate-limited scheduler on purpose (the bundle must be one stdlib-only file). |
| Tests | `uv run --frozen pytest` | All pass in random order, 100% line and branch coverage (`fail_under = 100`) of `staan_mcp` and `scripts/build_zipapp.py`. |
| Packaging | `uv build && uv run --frozen --only-group dist twine check --strict dist/* && uv run --frozen --only-group dist check-wheel-contents dist/*.whl` | sdist and wheel are valid and clean. The checkers come from the locked `dist` group. |
| Workflows | `uvx zizmor .github/workflows` | No findings. |
| Hooks | `uv run --frozen prek run --all-files` | Every hook passes (see the note below). Includes `detect-private-key` and gitleaks (secret scanning). CI runs the same command in the `metadata` job with `SKIP=mypy,pylint,xenon`. |
| Mutation | see below | Mutation score at or above 0.90 (CI schedule). |

Run all the fast gates in one go (Git Bash):

```bash
uv lock --check \
 && uv run --frozen ruff check . \
 && uv run --frozen ruff format --check . \
 && uv run --frozen mypy \
 && uv run --frozen pylint src tests scripts \
 && uv run --frozen xenon --max-absolute B --max-modules A --max-average A src \
 && uv run --frozen pytest
```

Notes:

- `pytest` is configured in `pyproject.toml` (random order through `pytest-randomly`, warnings are
  errors, `--strict-markers`). Add `-p no:randomly` to reproduce an ordering. The stdio smoke test
  spawns a subprocess and takes a few seconds.
- `tests/repo/` checks the packaging and CI configuration (workflows, Dockerfile, Dependabot,
  pre-commit, commitizen, version agreement between `pyproject.toml`, `server.json` and the
  `VERSION` line of `stdio_server.py`, and that `cz bump` rewrites it). It reads files only,
  needs no network, and is not part of the mutation run (it tests no `src` code). The bump test
  runs commitizen (in the `test` dependency group, so a `--group test` environment has it), which
  calls `git`: without `git` on `PATH` (a slim container) it is skipped with that reason, not
  failed.
- Pylint needs to know where the tests' imports live: `build_zipapp` (`scripts/`) and the helper
  modules next to the tests (`rpc_process`, `repo_files`, ...). pytest finds them through
  `pythonpath` and the rootdir `conftest.py`; pylint only added a directory that was itself a
  command-line argument, so `pylint src tests` (no `scripts`) or a single test file failed with
  E0401 while `pylint src tests scripts` passed. `init-hook` in `[tool.pylint.main]` now puts
  both directories on `sys.path` for every invocation.
- Hypothesis tests must stay cheap to generate: a test fails with `FailedHealthCheck: too_slow`
  when fewer than 10 valid inputs come out of the first second of drawing, and a CPU shared with
  other work is enough to hit that. Use one `st.text` draw rather than `st.from_regex` or many
  small draws per element, and check a new strategy with `pytest --hypothesis-show-statistics`
  (the data-generation time per example should stay in single-digit milliseconds). The property
  tests were run 50 times on Python 3.11 in a container limited to one CPU shared with busy loops.
- `scripts/build_zipapp.py` builds the single-file release bundle `staan-mcp-stdio.pyz`
  (`uv run python scripts/build_zipapp.py`, output in `dist-bundle/`). `scripts/` is inside the
  ruff, mypy, pylint and coverage gates (`pythonpath = ["scripts"]` and `--cov=build_zipapp` in
  `pyproject.toml`); it is outside xenon, which gates `src`, and outside the mutation run, which
  mutates `src`. Its tests are in `tests/repo/` (they test no `src` code): `test_build_zipapp.py`
  (the import-graph walk, rejected imports, archive layout, reproducibility) and
  `test_stdio_bundle.py` (the built archive on `python -I -S` from another directory: handshake,
  `tools/list`, a tool call, exit status, and that the archive holds exactly the modules a run
  imports). The release workflow can only run on a tag, so it is checked statically (actionlint,
  zizmor and `tests/repo/test_release_workflow.py`, which pins the wiring: built without a token,
  kept out of `dist/` so PyPI never sees it, attested, attached to the release). In the release
  the file is also built twice and compared byte for byte, and smoke-tested (`--version` and a
  handshake checked with `jq`) before it is uploaded.
- `stdio_server.py` is tested three ways: `tests/unit/test_stdio_*.py` run it in process against a
  loopback fake upstream (`tests/local_staan.py`) and give it 100% line and branch coverage;
  `tests/integration/test_stdio_parity.py` is the drift guard that compares it with the package on
  the same inputs; `tests/integration/test_stdio_standalone.py` proves it runs on a bare
  interpreter with no third-party import, and that a copy of the file alone prints one line and
  exits with status 2. The tests that start `python -m staan_mcp`, the script and the bundle as
  subprocesses take a few seconds.
- The request rate limiter (`staan_mcp.ratelimit`, `STAAN_RATE_LIMIT`) is tested on a fake clock
  (`tests/unit/test_ratelimit.py`, including a Hypothesis check of the invariant and a
  multi-threaded one; `test_client_rate_limit.py`, `test_stdio_rate_limit.py`) and end to end with
  real time (`tests/integration/test_rate_limit_package.py` in process, `test_rate_limit_stdio.py`
  on `python -m staan_mcp` and on `stdio_server.py` as subprocesses, about 45 s together). The end
  to end tests never judge by how long they slept: `tests/local_staan.py` stamps each request's
  arrival with `time.monotonic()` and the tests assert that no half-open window of 0.8 s (the
  second Staan counts in, less `SCHEDULER_TOLERANCE` = 0.2 s for the lag of the thread that stamps
  an arrival on a loaded machine; the limiter itself adds a 50 ms margin) holds more than the
  limit, that every request still succeeded and that the burst was spread over more than one
  window. Keep the real-time tests at rates of 5 to 20 per second; the fake's listen backlog is
  long on purpose, so a busy machine cannot make a client's connection retry arrive a second late.
  Stamps taken on a machine starved far beyond that (more than twice as many busy processes as
  cores) can still be a second late, which no test of arrival times can distinguish from a bug.
- `tests/live/` talks to the real Staan API and is skipped unless `STAAN_API_KEY` is set and
  `--run-live` is passed: `uv run pytest tests/live --run-live --no-cov`. CI never runs it.
- The reference template's hook file is stored as
  `docs/research/python-packaging-best-practices-templates/pre-commit-config.yaml` (no leading
  dot): prek discovers nested `.pre-commit-config.yaml` files and would run them as separate
  projects.
- The hooks come from `.pre-commit-config.yaml`; `prek` is a drop-in replacement for `pre-commit`.
  `prek run --all-files` only sees files that git tracks, so on a repository with no commits yet
  pass them explicitly: `prek run --files $(git ls-files --others --exclude-standard)`.
  The `typos` hook skips `tests/fixtures/*.json` (captured upstream text in other languages), set
  in `[tool.typos.files]`. The hooks need network access on the first run to fetch their
  environments, and the `mixed-line-ending` hook normalises files to LF (see `.gitattributes`).
- `zizmor` runs offline without a token. Set `GH_TOKEN` to enable its online audits.
- gitleaks reads `.gitleaks.toml`: the default rules plus one allowlist entry for the exact fake
  key the tests share (`test-key-0123456789`, rule `generic-api-key` only), which the default
  rule flags and which is already in the history. Anything else it reports is a real finding:
  examples in code and docs use a placeholder such as `your-staan-api-key`, not a key-shaped
  string. To run it as CI does: `gitleaks git --redact --verbose .` (history) or
  `gitleaks dir --redact --verbose .` (working tree; it does not honour `.gitignore`, so exclude
  the local key files first).

## Mutation testing (mutmut)

mutmut needs `fork()`, so it does not run on native Windows. CI runs it on Linux every week
(`.github/workflows/mutation.yml`). Locally on Windows, use Docker (or WSL, where plain
`uv run mutmut run` works). `src/staan_mcp/stdio_server.py` is the one module that mutmut skips
(`do_not_mutate` in `pyproject.toml`): its threads, sockets and timeouts make mutation runs slow
and flaky, and it is covered by the other gates, including 100% coverage. The shared modules it
uses (`contract`, `upstream`, `payloads`, `sources`, ...) are mutated like the rest of `src`.

The script below copies the repository into the container (so the host tree stays untouched and
the API key files, which must never leave your machine, are excluded), installs the locked test
and mutation groups, and runs mutmut against `tests/unit` and `tests/integration` (see
`[tool.mutmut]` in `pyproject.toml`). Save it as `.tmp/mutmut/run.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
mkdir -p /work/p && cd /work/p && rm -rf mutants   # always re-test: mutmut caches by source hash only
tar -C /src --exclude='*key*.txt' --exclude=testing_endpoint.txt --exclude=.venv --exclude=.git \
    --exclude=.tmp --exclude=mutants --exclude=dist --exclude='__pycache__' --exclude='.*_cache' \
    --exclude=.hypothesis --exclude=docs/research -cf - . | tar xf -
export UV_PROJECT_ENVIRONMENT=/work/venv UV_LINK_MODE=copy UV_NO_SYNC=1  # no default `dev` resync
uv sync --frozen --no-default-groups --group test --group mutation
uv run --frozen mutmut run || true
uv run --frozen mutmut export-cicd-stats
cp mutants/mutmut-cicd-stats.json /out/
uv run --frozen mutmut results > /out/survivors.txt || true
for m in $(grep -E ': (survived|timeout)' /out/survivors.txt | sed 's/: .*//' | tr -d ' '); do
  uv run --frozen mutmut show "$m" 2>&1 | grep -E '^[-+] ' ; echo "== $m"
done > /out/show.txt
```

Then, from Git Bash in the repository root (the `MSYS_NO_PATHCONV` and `pwd -W` bits keep Git
Bash from rewriting the mount paths):

```bash
mkdir -p .tmp/mutmut/out
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$(pwd -W):/src:ro" \
  -v "$(pwd -W)/.tmp/mutmut:/mm:ro" \
  -v "$(pwd -W)/.tmp/mutmut/out:/out" \
  ghcr.io/astral-sh/uv:python3.13-bookworm bash /mm/run.sh
```

Results land in `.tmp/mutmut/out/`: `mutmut-cicd-stats.json`, `survivors.txt` and `show.txt` (the
diff of every survivor and timeout, each followed by a `== <mutant>` line). The score is
`(killed + timeout) / (total - skipped)`. The Docker Desktop daemon must be running (start it
first: `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"`).

Latest local result (2480 mutants, from the verification run before the tests listed below were
added; not re-run since): 2431 killed, 1 timeout, 48 survived, a score of 98.1% against the CI
minimum of 90%. Since that run, tests were added for the `strip("XX[]XX")` mutant of
`is_loopback` and `_bind_host`, for `rstrip("XX/XX")` on the base URL, and for the
`zip(strict=True)` invariant of the shared payload helpers; those mutants should now be killed.
A timeout counts as killed: the mutant hangs (for example
`index = 1` instead of `index -= 1` in a loop, or `type=None` on `--transport`, which makes
`main` start a real server), and the test run is stopped by mutmut's time limit.

Writing tests that work under mutmut:

- mutmut replaces `sys.stderr` and rewrites function signatures behind a trampoline. A test that
  relies on `sys.stderr` at import time (the MCP stdio client does) must pass an explicit file,
  see `tests/integration/test_stdio.py`. A test that expects `TypeError` from a wrong call must
  `await` the call inside `pytest.raises`, see `test_signature_forbids_positional_arguments`.
- Assert exact messages (`==`, not `in`): an `"XX...XX"` mutant still contains the original text.
- mutmut runs the suite from a `mutants/` copy of the tree whose `staan_mcp/__init__.py` imports
  mutmut, so a test that starts the package or the script on a bare interpreter (`python -I -S`,
  no site-packages) cannot hold there and would abort the clean run. Such a test skips itself
  when `mutmut_skip_reason()` (`tests/conftest.py`) finds `MUTANT_UNDER_TEST` in the environment,
  which mutmut sets in every process it runs; it runs in every other run. See
  `needs_the_pristine_package` in `tests/integration/test_stdio_standalone.py`.
- Add `-v staan-mm:/work` to the `docker run` command to keep the virtual environment between
  runs; the script wipes `mutants/` itself, so results are never stale.

The remaining survivors were reviewed and are equivalent or unobservable:

- HTTP header names in other case (`authorization`, `ACCEPT`): header names are case-insensitive.
- Default values in the tool function signatures (`mode="short"`, `offset=0`, ...): mutmut keeps
  the original signature on its trampoline, so the mutated default is never used. The
  advertised defaults are pinned by `tests/integration/test_server_contract.py` instead.
- `zip(..., strict=...)` in `payloads._failures` and `_raw_responses`: the callers always pass
  equally long sequences, so a mutant that drops the check changes no output.
  `tests/unit/test_mutation_shared.py` pins the invariant (mismatched lengths raise
  `ValueError`), which should kill the `strict` mutants that remove or weaken the check.
- `ensure_ascii=None`, `follow_redirects=None`, `stream=None`, `type="text"` removal,
  `structured_output=None`: each equals the default the code relied on.
- `logging.basicConfig(level=..., stream=...)` in `cli._configure_logging`: the call that follows
  (`setLevel`) sets the level anyway, and `stream=None` falls back to `sys.stderr`.
- `start = None` in `formatting._insert_markers`: `answer[None:i]` equals `answer[0:i]`.
- `retry_after`: `seconds <= 0` (adding zero seconds is the same as adding none), and a header
  default of `"XXXX"` (`float()` rejects it like it rejects `""`).
- The `timeout` argument of the non-timeout `RequestError` branch in `StaanClient._attempt`: it is
  only printed for `TimeoutException`, which the earlier `except` clause always catches.
- Boundaries that give the same result (`retry-after: 0`, `float("")` versus `float("XXXX")`),
  `maxsplit`/`count` arguments on strings that contain a single match, codec name case
  (`"IDNA"`, `"ASCII"`), and `cast()` type strings (not evaluated at runtime).
- `maxsplit`/`count` on a single anchored regex match in `domains._extract_host`
  (mutmut_9, 11, 17 and 19): `_SCHEME` is anchored with `^`, `_PORT` with `$`, and the path
  split only keeps `[0]`, so a different `count` or `maxsplit` yields the same host.

A surviving mutant is either a missing assertion (fix the test) or an equivalent mutant (as
above). Never silence one with `# pragma: no mutate` without a comment saying why it is
equivalent.

## Consistency review checklist

When you change a tool, a parameter, a default, an environment variable or a flag, update all of
these in the same change:

- `src/staan_mcp/server.py` and `src/staan_mcp/cli.py` (the code is the source of truth),
- `docs/DESIGN.md` (the interface contract),
- `README.md` (tool reference, configuration tables, Docker section),
- `server.json` (environment variables a registry client should offer),
- the `--help` epilog in `cli.py`,
- `CHANGELOG.md` under "Unreleased".

## Secrets

API keys live in local `*key*.txt` files, which are git-ignored and excluded from the Docker
build context. They are read only at runtime by live probes. No key value may appear in a test,
fixture, log or document. To check, search the tree for each key value (without printing it) and
expect zero hits outside the key files.
