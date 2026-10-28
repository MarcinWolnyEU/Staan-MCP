"""Rules every workflow job follows: pinned tools, isolated environments, time limits."""

from __future__ import annotations

import re
import shlex
from typing import Any

import pytest
from repo_files import (
    all_jobs,
    jobs,
    load_yaml,
    pyproject,
    read_text,
    run_lines,
    server_json,
    step_named,
    steps,
    workflow,
)

UV_GROUP_PIN = re.compile(r"^uv==(?P<version>\d+\.\d+\.\d+)$")
UV_BUILD_PIN = re.compile(r"^uv_build==(?P<version>\d+\.\d+\.\d+)$")
# A requirement on the `uv` package itself (`uv`, `uv==1`, `uv[x]`), not `uvicorn` or `uv-build`.
UV_REQUIREMENT = re.compile(r"^uv(?![\w.-])", re.IGNORECASE)
JOB_IDS = [f"{name}:{job_id}" for name, job_id, _ in all_jobs()]


def _uv_version() -> str:
    """The version Dependabot bumps and setup-uv reads: the `uv-version` dependency group."""
    pins = pyproject()["dependency-groups"]["uv-version"]
    assert len(pins) == 1
    match = UV_GROUP_PIN.match(pins[0])
    assert match is not None
    return match.group("version")


@pytest.mark.parametrize(("name", "job_id", "job"), all_jobs(), ids=JOB_IDS)
def test_every_job_has_a_time_limit(name: str, job_id: str, job: dict[str, Any]) -> None:
    if "uses" in job:  # a reusable-workflow call; the called jobs carry their own limits
        assert name == "release.yml"
        assert job_id == "ci"
        return
    minutes = job.get("timeout-minutes")
    assert isinstance(minutes, int)
    assert 0 < minutes <= 90


@pytest.mark.parametrize(("name", "job_id", "job"), all_jobs(), ids=JOB_IDS)
def test_jobs_that_sync_a_narrow_environment_do_not_resync(
    name: str, job_id: str, job: dict[str, Any]
) -> None:
    # `uv run` syncs the default `dev` group unless told not to, which would undo
    # `uv sync --no-default-groups --group ...` and install every dev tool.
    syncs = [line for line in run_lines(job) if line.startswith("uv sync")]
    env = job.get("env", {})
    if syncs:
        assert all("--no-default-groups" in line for line in syncs), (name, job_id)
        assert env.get("UV_NO_SYNC") == "1", (name, job_id)
    else:
        assert "UV_NO_SYNC" not in env, (name, job_id)


def test_every_uvx_tool_is_pinned() -> None:
    commands = [
        line for _, _, job in all_jobs() for line in run_lines(job) if line.startswith("uvx ")
    ]
    assert commands, "expected at least one uvx call"
    for command in commands:
        args = shlex.split(command)[1:]
        if args[0] == "--from":
            assert "==" in args[1], command
        else:
            assert "==" in args[0] or "@" in args[0], command


def test_no_workflow_runs_an_unpinned_python_tool() -> None:
    lines = [line for _, _, job in all_jobs() for line in run_lines(job)]
    for tool in ("twine", "check-wheel-contents", "pip-audit"):
        users = [line for line in lines if re.search(rf"\b{re.escape(tool)}\b", line)]
        assert users, tool
        assert all(line.startswith("uv run --only-group ") for line in users), users


def test_every_setup_uv_step_reads_the_uv_version_from_pyproject() -> None:
    count = 0
    for _, _, job in all_jobs():
        for step in steps(job):
            if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                count += 1
                # An explicit file makes a missing pin an error; without one setup-uv falls back
                # to the latest uv.
                assert step["with"]["version-file"] == "pyproject.toml", step
                assert "version" not in step["with"], step
    assert count >= 8


def test_setup_uv_finds_exactly_one_uv_pin_in_pyproject() -> None:
    config = pyproject()
    # `required-version` would take precedence over the pin that Dependabot bumps.
    assert "required-version" not in config["tool"]["uv"]
    extras = config["project"].get("optional-dependencies", {}).values()
    groups = config["dependency-groups"].values()
    requirements = [
        *config["project"].get("dependencies", []),
        *(r for extra in extras for r in extra),
        *(r for group in groups for r in group if isinstance(r, str)),
    ]
    # setup-uv takes the first match, and Dependabot mishandles a package listed twice.
    assert [r for r in requirements if UV_REQUIREMENT.match(r)] == [f"uv=={_uv_version()}"]


def test_the_uv_version_group_is_never_synced() -> None:
    config = pyproject()
    entries = [r for group in config["dependency-groups"].values() for r in group]
    included = {r["include-group"] for r in entries if isinstance(r, dict)}
    # A synced copy would put a second uv in the environment, shadowing the one setup-uv installs.
    assert "uv-version" not in included
    assert config["tool"]["uv"]["default-groups"] == ["dev"]
    assert "uv-version" not in config["tool"]["uv"]["default-groups"]


def test_every_uv_pin_is_exact_and_they_share_a_major_version() -> None:
    # Dependabot bumps each pin through its own ecosystem and they may differ for a while (the
    # build backend is fetched in isolation, so it need not match the uv that runs it), so the
    # minor and patch versions are not compared.
    build = UV_BUILD_PIN.match(pyproject()["build-system"]["requires"][0])
    hook = re.search(
        r"repo: https://github\.com/astral-sh/uv-pre-commit\n\s+rev: [0-9a-f]{40} "
        r"# frozen: (\d+\.\d+\.\d+)\n",
        read_text(".pre-commit-config.yaml"),
    )
    image = re.search(
        r"^FROM ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64} ",
        read_text("Dockerfile"),
        re.MULTILINE,
    )
    assert build is not None
    assert hook is not None
    assert image is not None
    versions = {
        "uv-version group": _uv_version(),
        "uv_build": build.group("version"),
        "uv-pre-commit": hook.group(1),
        "Dockerfile": image.group(1),
    }
    assert len({version.split(".")[0] for version in versions.values()}) == 1, versions


def test_ci_gate_waits_for_every_required_job() -> None:
    ci_jobs = jobs(workflow("ci.yml"))
    gate = ci_jobs["ci-ok"]
    assert gate["if"] == "always()"
    # Codecov is advisory (fail_ci_if_error: false) and skipped in the release run.
    assert set(gate["needs"]) == ci_jobs.keys() - {"ci-ok", "codecov"}
    assert "metadata" in gate["needs"]


def test_metadata_job_runs_the_hook_only_checks() -> None:
    job = jobs(workflow("ci.yml"))["metadata"]
    versions = step_named(job, "Check pyproject.toml and server.json versions agree")
    jq_filter = "'.version == $v and (.packages | all(.version == $v))'"
    assert versions["run"].splitlines() == [
        'version="$(uv version --short)"',
        f'jq -e --arg v "${{version}}" {jq_filter} server.json',
    ]
    hooks = step_named(job, "Run the pre-commit hooks on every file")
    assert hooks["env"] == {"SKIP": "mypy,pylint,xenon"}
    assert hooks["run"] == (
        "uv run --only-group release prek run --all-files --show-diff-on-failure"
    )
    title = step_named(job, "Check the pull request title is a Conventional Commit")
    assert title["if"] == "github.event_name == 'pull_request'"
    # Passed through the environment, never interpolated into the script (injection).
    assert title["env"] == {"PR_TITLE": "${{ github.event.pull_request.title }}"}
    assert title["run"] == 'uv run --only-group release cz check --message "${PR_TITLE}"'


def test_hooks_skipped_in_ci_exist_and_run_in_the_lint_job() -> None:
    config = load_yaml(".pre-commit-config.yaml")
    local = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}
    assert {"mypy", "pylint", "xenon"} <= local
    lint = " ".join(run_lines(jobs(workflow("ci.yml"))["lint"]))
    for command in ("uv run mypy", "uv run pylint src tests", "uv run xenon"):
        assert command in lint


def test_server_json_version_matches_pyproject() -> None:
    version = pyproject()["project"]["version"]
    metadata = server_json()
    assert metadata["version"] == version
    assert [package["version"] for package in metadata["packages"]] == [version]
