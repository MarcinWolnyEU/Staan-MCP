# Python packaging best practices (as of 2026-09-30)

Research for Staan-MCP (PyPI `staan-mcp`, repo `MarcinWolnyEU/Staan-MCP`). Every version below was read
from the PyPI JSON API or the GitHub API on 2026-09-30. Everything marked **VERIFIED** was executed in
scratch (`.tmp/python-packaging-best-practices/`, project `tc/`, plus Linux containers for mutmut/Docker).
Copy-paste templates that passed those checks live in
`docs/research/python-packaging-best-practices-templates/` (pyproject, workflows, pre-commit, Dockerfile,
dependabot, server.json). Read the "Gotchas" section before using them.

## 0. Findings that change the plan

1. **`mcp` is 2.2.0 (2026-09-07).** `pip install mcp` gives 2.x (`FastMCP` is now `MCPServer`; v1.30.0 is security-only).
   2.x depends on **`httpx2`** (Pydantic-stewarded continuation of httpx), not `httpx`. httpx 0.28.1 was last released 2024-12-06.
2. **respx does not intercept httpx2** (VERIFIED: `ConnectError`). pytest-httpx pins `httpx==0.28.*`. `httpx2.MockTransport`
   exists and works (VERIFIED). Recommendation: use `httpx2` for the Staan client (already in the dependency tree via mcp,
   zero extra deps) and test with `httpx2.MockTransport`, not respx. If the project ever uses `httpx`, respx 0.23.1 is fine.
3. **Python 3.10 reaches EOL in October 2026** (this month). Use `requires-python = ">=3.11"`. An MCP server is normally launched
   through `uvx`, which downloads the interpreter, so a higher floor costs users nothing.
4. **mutmut cannot run on native Windows** (VERIFIED: it prints "please use the WSL", upstream issue boxed/mutmut#397).
   This machine has no Ubuntu WSL distro (only docker-desktop) but Docker works, so run mutmut in a Linux container locally
   and on `ubuntu-latest` in CI. Never in the Windows matrix.
5. **mutmut collides with `--cov` in pytest `addopts`** (VERIFIED). With `fail_under = 100` the stats run reports
   "failed to collect stats" and every mutant stays "not checked". Fix in `[tool.mutmut]`:
   `pytest_add_cli_args = ["--no-cov", "-p", "no:randomly"]` (then it works: 15/17 killed on the demo). Alternatively keep
   `--cov` out of `addopts` and pass it explicitly in CI.
6. **`mutmut run` exits 0 even with survivors** (VERIFIED). Gate on `mutmut export-cicd-stats` ->
   `mutants/mutmut-cicd-stats.json` (`killed/survived/total/no_tests/skipped/suspicious/timeout/segfault`) with a score check.
7. **The MCP registry `description` is limited to 100 characters** (server.schema.json `maxLength: 100`); `name` pattern
   `^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`; `version` must not be a range. Validate `server.json` in pre-commit with check-jsonschema
   against `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json` (latest dated schema; VERIFIED, it
   caught my stub missing `description`/`transport`).
8. **Ruff 0.16.0 (2026-07-23) enabled 413 rules by default** (was 59), formats Markdown code blocks by default, adds
   `# ruff: ignore[...]`. `select = ["ALL"]` still works unchanged.
9. **uv 0.12.x**: `uv_build` upper-bound is `<0.13`. The locally installed uv is 0.8.10; run 0.12.21 without touching the system
   install via `uvx uv@0.12.21 ...` (wrapper: `.tmp/python-packaging-best-practices/uv12.sh`). With `[tool.uv] required-version`
   the old uv refuses to run (VERIFIED), which is a nice guard.
10. `staan-mcp` is **free on PyPI** (404 on 2026-09-30) and the GitHub repo `MarcinWolnyEU/Staan-MCP` **does not exist yet**
    (owner is a User, not an Org, so the registry namespace is `io.github.MarcinWolnyEU/...`, verified via GitHub auth docs).

## 1. Tool versions (PyPI, 2026-09-30)

| Tool | Version | Notes |
|---|---|---|
| uv / uv_build | 0.12.21 (2026-09-29) | build-backend `uv_build>=0.12.21,<0.13` |
| hatchling | 1.32.4 | alternative backend; hatch-vcs 0.5.0, uv-dynamic-versioning 0.14.1 |
| ruff | 0.16.9 | lint + format |
| mypy | 2.3.1 | gate, `strict = true` |
| pyright | 1.1.414 | alternative |
| ty | 0.0.84 | Astral, **beta**, still 0.0.x; advisory only |
| pytest / pytest-cov / coverage | 9.1.1 / 7.1.0 / 7.16.2 | pytest 9 supports native `[tool.pytest]` TOML (VERIFIED) |
| anyio | 4.15.1 | built-in pytest plugin, zero extra dep (VERIFIED) |
| pytest-asyncio | 1.4.0 | fine, but unnecessary because mcp already needs anyio |
| pytest-randomly | 5.0.0 | disable under mutmut |
| hypothesis | 6.168.3 | |
| inline-snapshot | 0.35.4 | |
| respx / pytest-httpx | 0.23.1 / 0.36.2 | httpx 0.28 only, useless with httpx2 |
| radon | 6.0.1 (2023-03-26, unmaintained but works) | VERIFIED on py3.13/3.14 with `match`, PEP 695, `except*`, async comps |
| xenon | 0.9.3 (2024-10-21) | radon wrapper that fails builds on thresholds |
| pylint | 4.1.1 | |
| mutmut | 3.8.0 (2026-09-12) | Linux/macOS/WSL only |
| pre-commit / prek | 4.6.2 / 0.5.4 | prek is a Rust drop-in, same config file (VERIFIED, ran all hooks) |
| commitizen | 4.19.0 | `version_provider = "uv"` updates pyproject and uv.lock (VERIFIED) |
| python-semantic-release | 10.7.0 | alternative to commitizen |
| towncrier | 26.9.0 | alternative changelog fragments |
| zizmor | 1.30.1 | workflow security linter |
| actionlint (actionlint-py) | 1.7.12 | passes on my workflows |
| twine / build / check-wheel-contents | 7.0.0 / 1.6.1 / 0.6.3 | |
| pip-audit | 2.10.1 | |
| cyclonedx-bom | 7.5.0 | alternative SBOM generator |
| validate-pyproject | 0.26 | |
| sp-repo-review | 2026.8.14 | scikit-hep scorecard for repo hygiene (VERIFIED) |
| typos | 1.50.3 | spell checker (Rust) |
| mkdocs-material / zensical | 9.7.7 / 0.0.66 | mkdocs-material is in maintenance mode, EOL 2026-11-05 (extended to 2027-05-05); Zensical 0.1.0 planned 2026-11-05 |
| mcp / httpx2 / pydantic | 2.2.0 / 2.13.1 / 2.13.5 | |

CPython status (devguide): 3.10 security, EOL Oct 2026; 3.11 EOL Oct 2027; 3.12 EOL Oct 2028; 3.13 and 3.14 bugfix;
3.15 prerelease (uv can install `3.15.0rc2`; final due Oct 2026). Support **3.11-3.14**, add 3.15 to the matrix as an
allowed-failure job after its final release, and add the classifier only once it passes.

## 2. Recommended toolchain and decisions

### Project layout and build

- `uv init --package` style, **src layout**: `src/staan_mcp/{__init__,__main__,py.typed,...}`, `tests/`, `docs/`.
  Console script `staan-mcp = "staan_mcp:main"` plus `python -m staan_mcp` (so `uvx staan-mcp` works).
- **Build backend: `uv_build`** (stable, semver, pure-Python only, no build scripts, default module path
  `src/<normalized_name>/__init__.py`). VERIFIED: sdist has only `pyproject.toml`, README, LICENSE, `src/`; wheel has
  `py.typed` and `dist-info/licenses/LICENSE`; `twine check --strict`, `check-wheel-contents`, `validate-pyproject` all pass.
  Add `[tool.uv.build-backend] source-include = ["tests/**"]` only if distro packagers need tests in the sdist.
- Use hatchling + hatch-vcs instead only if you want tag-derived versions. `uv_build` does **not** support dynamic versions.
- **PEP 621 metadata**: SPDX `license = "MIT"` + `license-files = ["LICENSE"]` (no license classifier; sp-repo-review PP005),
  classifiers incl. `Typing :: Typed`, `Framework :: AsyncIO`, `[project.urls]` (Homepage, Issues, Changelog, Source).
- **PEP 735 dependency groups**: `test`, `lint`, `mutation`, `dev` (uses `include-group`), `[tool.uv] default-groups = ["dev"]`.
  Commit `uv.lock`. CI sets `UV_FROZEN=1` and separately runs `uv lock --check`.
- MCP registry ownership line must be in the README that becomes the PyPI description:
  `<!-- mcp-name: io.github.MarcinWolnyEU/staan-mcp -->` (own line or inside an HTML comment, never followed by a period).

### Versioning and changelog

- **Static version + commitizen + Conventional Commits.** `cz bump --yes` (VERIFIED end to end in a scratch git repo):
  bumps `pyproject.toml`, `uv.lock`, both `"version"` lines in `server.json` (`version_files = ["server.json:version"]`),
  writes/updates `CHANGELOG.md`, commits, creates annotated tag `v0.2.0`. `major_version_zero = true` keeps `feat!` at 0.x.
  The release workflow re-checks that tag, `uv version --short` and `server.json` agree before publishing.
- Alternative: release-please-action v5.0.0 (release PRs). Not tested.
- Commit-msg hook via the commitizen pre-commit hook; PR title check optional.

### Lint, format, types

- **Ruff**: `select = ["ALL"]`, ignore `COM812, D203, D213, CPY001`, google docstring convention, tests ignore
  `S101, PLR2004, D, ANN, INP001`. `src` setting is no longer needed (sp-repo-review RF003). Target version is inferred from
  `requires-python`. Run `ruff check --output-format=github` and `ruff format --check` in CI.
- **Types**: `mypy` (`strict = true`, `warn_unreachable`, `enable_error_code = ["ignore-without-code","redundant-expr","truthy-bool"]`)
  is the blocking gate; it passed on the demo. **ty 0.0.84** also passed but is beta: run it as an advisory (non-required)
  job. Pyright is a fine substitute but there is no reason to run three checkers. Ship `py.typed`.
- **Pylint 4.1.1** config that reached 10.00/10 (VERIFIED): `jobs = 0`, `py-version`, plugins `pylint.extensions.mccabe` and
  `docparams`, `fail-under = 10.0`, enable `useless-suppression`; disable `missing-module-docstring`,
  `wrong-import-order`, `ungrouped-imports` (ruff owns those); `[tool.pylint.basic] no-docstring-rgx = "^(_|test_)"`.
  Note `no-docstring-rgx` belongs in `basic`, not `messages control`.
- **Radon/xenon**: radon reads `[tool.radon]` from `pyproject.toml` in the **current directory** (VERIFIED; `cc_min = "B"`
  hides A blocks, so `-a` prints nothing when everything is A). Enforcement is xenon, CLI flags only:
  `xenon --max-absolute B --max-modules A --max-average A src`. Also `radon mi -s src` (MI grade A means >= 20).
  Ruff `C901`/`PLR09xx` and pylint mccabe overlap; radon/xenon is the explicit gate the user asked for.

### Tests

- pytest 9 native TOML: `[tool.pytest]` with `minversion = "9.0"`, `testpaths`, `addopts = ["-ra","--strict-config","--strict-markers"]`,
  `xfail_strict = true`, `filterwarnings = ["error"]`, `log_level = "INFO"` (VERIFIED, satisfies sp-repo-review PP30x).
- **anyio plugin** instead of pytest-asyncio: `pytestmark = pytest.mark.anyio`; the default `anyio_backend` fixture is asyncio-only
  (VERIFIED, async fixtures work). Add `anyio_backend` param only if trio support matters.
- Coverage: `[tool.coverage.run] branch = true, source_pkgs = ["staan_mcp"]`, `[tool.coverage.report] fail_under = 100,
  show_missing, skip_covered, exclude_also = ["if TYPE_CHECKING:", "@overload", "raise NotImplementedError", '^\s*\.\.\.$']`.
  Use a TOML literal string (single quotes) for the regex; `"\.\.\."` is a TOML parse error (hit it).
- Cross-OS coverage: each matrix job runs `pytest --cov --cov-fail-under=0` with `COVERAGE_FILE=.coverage.<os>.<py>`, uploads it;
  one `coverage` job downloads all, `coverage combine`, `coverage report --fail-under=100`, uploads to Codecov via OIDC.
- Also useful: hypothesis (property tests for market expansion/URL stripping), inline-snapshot (`--inline-snapshot=review|fix|create`
  locally; default in CI fails on mismatch), pytest-randomly (its `-p no:randomly` goes into mutmut args), a `lowest-direct` job
  (`uv run --isolated --no-editable --resolution lowest-direct`) and optionally a `--prerelease=allow` canary job.
- **mutmut 3.8.0** VERIFIED in `python:3.13-slim` (Debian): works with src layout, `uv_build`, anyio/pytest-asyncio tests,
  `source_paths = ["src/"]`, `pytest_add_cli_args_test_selection = ["tests/"]`. Commands: `run` (resumable), `results`, `show`,
  `browse`, `export-cicd-stats`, `tests-for-mutant`, `apply`, `badge`, `print-time-estimates`. Pragmas: `# pragma: no mutate`
  (line), `no mutate block`, `no mutate start/end`. Mutants dir is `mutants/` (gitignore it). Optional keys: `mutate_only_covered_lines`,
  `do_not_mutate`, `also_copy`, `type_check_command` (mypy/pyrefly only), `process_isolation = "fork"|"forkserver"`,
  `max_stack_depth`. ~7-14 mutations/second on the toy project. Run weekly plus `workflow_dispatch`, gate at a score threshold.
- Local Windows run: `docker run --rm -v "$(pwd -W):/work" python:3.13-slim-bookworm sh script.sh` copying the tree to `/tmp` first
  (script at `.tmp/python-packaging-best-practices/run_mut.sh`). Set `MSYS_NO_PATHCONV=1` under Git Bash.
  The `ghcr.io/astral-sh/uv:<ver>-python3.13-bookworm-slim` tag for a brand-new uv release may not exist yet (0.12.21 did not);
  `pip install uv==0.12.21` in a `python:*-slim` image is the reliable route.

### Pre-commit

- **prek** (0.5.4) with a standard `.pre-commit-config.yaml` (also valid for pre-commit 4.6.2). VERIFIED: `prek validate-config`
  and `prek run --all-files` ran every hook. Hooks: pre-commit-hooks v6.0.0, ruff-pre-commit (`ruff-check`, `ruff-format`),
  uv-pre-commit `uv-lock`, typos, check-jsonschema (workflows, dependabot, `server.json`), zizmor-pre-commit, commitizen (commit-msg),
  local mypy/pylint/xenon via `uv run --frozen --group lint`. Revs are full SHAs with `# frozen: vX` comments.
- CI does not need pre-commit itself (CI runs the same tools directly); optionally enable pre-commit.ci (`ci:` block is present).

### GitHub Actions (all pinned by SHA; zizmor 1.30.1 and actionlint 1.7.12 report zero findings on the templates)

Pinned SHAs (resolved via GitHub API on 2026-09-30):

| Action | Tag | SHA |
|---|---|---|
| actions/checkout | v7.0.1 | 3d3c42e5aac5ba805825da76410c181273ba90b1 |
| astral-sh/setup-uv | v10.2.0 | c18668ad3cf93ea998bef934396af7bb5c839dc7 |
| actions/upload-artifact | v7.0.1 | 043fb46d1a93c77aae656e7c1c64a875d1fc6a0a |
| actions/download-artifact | v8.0.1 | 3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c |
| actions/cache | v6.1.0 | 55cc8345863c7cc4c66a329aec7e433d2d1c52a9 |
| actions/setup-python | v7.0.0 | 5fda3b95a4ea91299a34e894583c3862153e4b97 |
| pypa/gh-action-pypi-publish | v1.14.2 | dc37677b2e1c63e2034f94d8a5b11f265b73ba33 |
| github/codeql-action (init/analyze/upload-sarif) | v4.38.2 | 2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2 |
| ossf/scorecard-action | v2.4.4 | 2d1146689b8cda280b9bc96326124645441f03bc |
| codecov/codecov-action | v7.1.1 | 303a32d7a59b442fa8d48b6a1cc6825c09c847a5 |
| zizmorcore/zizmor-action | v0.6.4 | cc914d7f3750a2d13d75c7f184a1060aa0e9d482 |
| actions/attest-build-provenance | v4.2.2 | 4d101475d8b20a2381f78447822ac1eab6504dd8 |
| actions/attest (used by the wrapper) | v4.2.2 | 1e69f48acb82d1966a394da916b4c1698aa569d6 |
| actions/attest-sbom | v4.1.0 | c604332985a26aa8cf1bdc465b92731239ec6b9e |
| actions/dependency-review-action | v5.0.0 | a1d282b36b6f3519aa1f3fc636f609c47dddb294 |
| anchore/sbom-action | v0.24.2 | 3ad7283483fc7af8ff2b4ea19663c2d5ca935e26 |
| re-actors/alls-green | v1.3.0 | b5b5b37504aa4183270bd3d855c52a67f212be35 |
| hynek/build-and-inspect-python-package | v3.0.1 | 2abe76da66d0a6a4a227101f9348ee855797cfa5 |
| docker/setup-buildx-action | v4.4.1 | f87e5991a6d7451dcb8d9637bfbc97413f497069 |
| docker/build-push-action | v7.4.0 | c3c9e263c25d99ce0380d002d59b67737d91b0dc |
| docker/login-action | v4.6.0 | dbcb813823bdd20940b903addbd779551569679f |
| docker/metadata-action | v6.2.0 | dc802804100637a589fabce1cb79ff13a1411302 |
| sigstore/cosign-installer | v4.1.2 | 6f9f17788090df1f26f669e9d70d6ae9567deba6 |
| softprops/action-gh-release | v3.0.3 | efb35369e0ad2afab669f228072c1b0d510eae64 |
| step-security/harden-runner | v2.21.1 | e14015d583714f6e62063499dc959a02595150a1 |
| googleapis/release-please-action | v5.0.0 | 45996ed1f6d02564a971a2fa1b5860e934307cf7 |

Pre-commit repo SHAs: pre-commit-hooks v6.0.0 `3e8a8703...`, ruff-pre-commit v0.16.9 `a56c0b92...`, uv-pre-commit 0.12.21 `5403c160...`,
typos v1.50.3 `00f422f3...`, check-jsonschema 0.38.2 `0494aa7f...`, zizmor-pre-commit v1.30.1 `fa412071...`, commitizen v4.19.0 `3f5ccc87...`.
Full SHAs are in the pre-commit template.

Design:
- Every workflow: top-level `permissions: {}`, per-job minimal permissions, `persist-credentials: false`, `concurrency` with
  cancel-in-progress only for PRs, `UV_FROZEN=1`.
- **setup-uv v10**: `enable-cache` defaults to "auto" (on for GitHub-hosted runners, off for release/tag pushes and
  `pull_request_target`/`workflow_run`); cache key globs include `pyproject.toml` and `uv.lock`. In release jobs set
  `enable-cache: false` (zizmor `cache-poisoning`). Docs still show v10.1.0 in snippets; latest is v10.2.0.
- **ci.yml** jobs: `lint` (ruff, mypy, pylint, radon/xenon, `uv lock --check`), `test` (ubuntu/windows/macos x 3.11-3.14),
  `coverage` (combine, 100 percent, Codecov OIDC), `lowest-direct`, `build` (`uv build --no-sources`, `twine check --strict`,
  `check-wheel-contents`, install the wheel into a clean env and run the entry point), `docker` (build without push), and a final
  `ci-ok` gate using `re-actors/alls-green` (single required status check for the ruleset).
- **Codecov v7.1.1**: OIDC via `use_oidc: true` + `id-token: write` (token ignored). Tokenless upload is not supported for non-fork
  PRs otherwise. Dependabot PRs need `CODECOV_TOKEN` as a Dependabot secret unless OIDC works for them (unverified).
- **security.yml**: zizmor-action (SARIF to code scanning), CodeQL (`python` and `actions`, `build-mode: none`,
  `queries: security-extended`), dependency-review on PRs, `pip-audit` on the exported lock. **scorecard.yml**: OpenSSF Scorecard,
  `publish_results: true`, SARIF upload. zizmor default policy flags tag-pinned actions as **error** (VERIFIED with
  `actions/checkout@v4`), so SHA pinning is enforced by the linter; an `auditor`-persona run only adds pedantic notes (missing
  workflow names, permission comments, job concurrency).
- **Dependabot**: `package-ecosystem: uv` is GA (version updates March 2025, security updates December 2025) and updates `uv.lock`.
  Use `cooldown.default-days: 7` (uv docs recommend matching your `exclude-newer`), ecosystems `uv`, `github-actions`, `docker`,
  `pre-commit`. Known gap: `dependency-type` inside `groups` is ignored for uv (dependabot-core #13202), and PEP 735
  `[dependency-groups]` support is incomplete (dependabot-core #10847), so group by `patterns`/`update-types` only and verify that
  group-only dependencies (lint, test) actually get PRs. Fallback: Renovate, or a scheduled `uv lock --upgrade` PR workflow.
  Note `[tool.uv] exclude-newer = "7 days"` accepts relative values but fails resolution when your own lower bounds are younger than
  7 days (VERIFIED with fresh ruff/uv), so only enable it once bounds are older.

### Release pipeline (release.yml, tag `v*` push)

1. `build`: verify tag == `uv version --short` == `server.json` versions, `uv build --no-sources`, `twine check --strict`,
   CycloneDX 1.5 SBOM from the lock (`uv export --frozen --no-dev --no-emit-project --format cyclonedx1.5
   --preview-features sbom-export --output-file ...`; uv marks it **preview**, works, VERIFIED), `actions/attest-build-provenance`
   (GitHub artifact attestation, needs `id-token: write` + `attestations: write`), upload artifact `dist`.
2. `publish-pypi`: separate job, `environment: pypi`, only `id-token: write`, `pypa/gh-action-pypi-publish` (Trusted Publishing).
   **PEP 740 attestations are on by default** for trusted publishing (`attestations: false` disables). Inputs: `packages-dir`,
   `print-hash`, `skip-existing`, `repository-url` (TestPyPI). `uv publish --trusted-publishing always` also works and uploads
   attestations by default (`--no-attestations` to opt out; VERIFIED in `uv publish --help`); the PyPA action is the more common choice.
3. `github-release` (needs pypi): `gh release create "$TAG" dist/* sbom --verify-tag --generate-notes` creates the release with
   assets in one call, which is compatible with GitHub **immutable releases** (GA 2025-10-28; enable in repo settings; releases then
   get signed release attestations and assets/tags are locked). Immutability forbids editing assets afterwards, so do not use a
   "create draft then upload later" flow unless you publish the draft last.
4. `publish-mcp-registry` (needs pypi): download `mcp-publisher_linux_amd64.tar.gz` **and verify it** against
   `registry_<ver>_checksums.txt` (assets also ship `.sigstore.json`), then `./mcp-publisher login github-oidc` and
   `./mcp-publisher publish` (`id-token: write`, `contents: read`). Pin `v1.8.1` (latest 2026-08-06). "invalid audience" means the
   binary is too old. The registry checks the PyPI description for `mcp-name:`, so PyPI must already have that version (PyPI upload
   can lag a few seconds; add a retry loop if the first publish 4xx's; untested). Registry is in **preview**; `server.json` schema
   `2025-12-11`, `registryType: "pypi"`, `identifier: "staan-mcp"`, `transport: {"type": "stdio"}`, `runtimeHint: "uvx"` when arguments
   are present; env vars declared with `isSecret: true` (`STAAN_API_KEY`).
5. (not written into the template, untested) `docker` job to GHCR: `docker/metadata-action` + `docker/login-action` (GITHUB_TOKEN,
   `packages: write`) + `docker/build-push-action` with `provenance: mode=max`, `sbom: true`, then
   `actions/attest-build-provenance` with `push-to-registry: true`. For the registry you can additionally list an `oci` package.

**PyPI first-time setup**: publish nothing manually. On https://pypi.org/manage/account/publishing/ add a *pending* trusted publisher:
project `staan-mcp`, owner `MarcinWolnyEU`, repo `Staan-MCP`, workflow `release.yml`, environment `pypi`. First tag push creates
the project. Enable 2FA. Create GitHub environments `pypi` (required reviewer = you, deployment tags `v*` only) and optionally
`mcp-registry`.

### Docker (VERIFIED build and run, 44 MB image, runs as uid 10001)

Multi-stage: `ghcr.io/astral-sh/uv:${UV_VERSION}` copied in, builder `python:3.13-slim-bookworm` with
`UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_DEV=1`, dependency layer with cache/bind mounts
(`uv sync --locked --no-install-project --no-editable`), then project layer, runtime stage copies only `/app/.venv`,
non-root user, `ENTRYPOINT ["staan-mcp"]`. For HTTP mode add `EXPOSE 8000` (present), `CMD ["--transport","http","--host","0.0.0.0"]`
and a `HEALTHCHECK` that hits the MCP endpoint (adapt to the final CLI). Pin base image digests (zizmor `unpinned-images`, Dependabot
`docker` ecosystem keeps them fresh). `.dockerignore` excludes `.venv .git .tmp dist mutants`.

### Repo hygiene, docs, community files

- LICENSE: **MIT** (matches the MCP SDK ecosystem; Apache-2.0 only if you want the patent grant). SPDX in metadata.
- Files: `README.md` (with `mcp-name` comment, install via `uvx`/`pip`/Docker, client config snippets for Claude Desktop/Claude Code/
  Cursor/VS Code, env var table, tool reference, badges), `CHANGELOG.md` (commitizen-generated), `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md` (Contributor Covenant **3.0**; latest GitHub release tag of the repo is still 2.1, the 3.0 text is on
  contributor-covenant.org/version/3/0), `SECURITY.md` (enable private vulnerability reporting), `SUPPORT.md`, `CODEOWNERS`,
  `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.yml` (issue forms) + `config.yml`, `PULL_REQUEST_TEMPLATE.md`,
  `.gitattributes` (`* text=auto eol=lf`; on this Windows box git warned about LF/CRLF until added), `.editorconfig`, `.gitignore`.
- Docs: README plus `docs/` markdown; **no mkdocs-material** (maintenance mode). If a site is wanted later, use Zensical
  (reads `mkdocs.yml`; 0.1.0 planned 2026-11-05).
- Badges: PyPI version, Python versions, CI, Codecov, OpenSSF Scorecard, License, ruff, uv, mypy strict, mutation score (from
  `mutmut export-cicd-stats`, custom shields endpoint) and an MCP Registry link.
- `sp-repo-review` (`uvx --with 'sp-repo-review[cli]' repo-review .`) flagged only missing docs/pre-commit/task-runner/.gitignore/GH
  config on the bare demo; run it again on the real repo (checks PY, PP, GH, MY, PC, RF, SEC). Add `[tool.repo-review]` ignores
  for the task-runner check (PY007) if you don't want nox/tox (a `Justfile` or `[tool.poe]` is optional).
- Repo settings to apply after creating the repo (manual/gh): a branch ruleset on `main` (PR required, required status check
  `CI passed`, linear history, block force-push/deletion, optional signed commits), a tag ruleset for `v*` (no update/delete),
  immutable releases on, Dependabot alerts + security updates on, secret scanning + push protection on, private vulnerability
  reporting on, code scanning default via the workflows above, workflow permissions default read-only, "require approval for
  first-time contributors", Codecov app installed. Use Repository Rules (not classic branch protection) so Scorecard can read them
  with the default GITHUB_TOKEN.

## 3. Gotchas and unverified items

- TOML: use literal strings (`'...'`) for regexes in `exclude_also`.
- `uv sync` on a Windows checkout prints a hardlink warning across drives; set `UV_LINK_MODE=copy` locally.
- radon `cc_min` in `[tool.radon]` silently hides everything below the grade, so an empty report is a pass, not a failure.
- mutmut + `--cov`/randomly/xdist: see section 0 item 5. Mutation testing also mutates `print`/string literals, so expect survivors in
  CLI text unless tests assert exact output.
- `radon` is unmaintained (last release 2023); if it breaks on a future Python, fall back to ruff `C901`/pylint mccabe
  (`max-complexity`) and `complexipy`. Pin `radon>=6.0.1,<7`.
- Not verified: Dependabot handling of dependency-groups-only packages, Codecov OIDC on Dependabot PRs, the GHCR/attestation docker
  job, retry behavior of the registry right after a PyPI upload, `mcp-publisher` on the exact final `server.json`.
- `ty` reached "beta" but is 0.0.x; keep it non-blocking.
- Astral's `uv:` Docker image tags lag new uv releases by hours to days.

## 4. Scratch artifacts

`.tmp/python-packaging-best-practices/`: `versions.py` (PyPI table), `gh.py`/`gh2.sh` (action SHAs), `tc/` (validated demo project
with the whole toolchain), `mutexp/` (first mutmut experiment), `run_mut.sh`, `uv12.sh`, `reg/` (downloaded MCP registry docs and schema).
