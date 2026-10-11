"""Load the repository's packaging and CI files for the tests in ``tests/repo``.

These tests check the release machinery (workflows, Dockerfile, Dependabot, pre-commit,
commitizen), which no other test exercises and which would otherwise only fail on GitHub.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def read_text(relative: str) -> str:
    """Return the text of a repository file."""
    return (ROOT / relative).read_text(encoding="utf-8")


def load_yaml(relative: str) -> dict[str, Any]:
    """Parse a YAML file of the repository into a mapping."""
    return cast("dict[str, Any]", yaml.safe_load(read_text(relative)))


def pyproject() -> dict[str, Any]:
    """Parse ``pyproject.toml``."""
    return tomllib.loads(read_text("pyproject.toml"))


def server_json() -> dict[str, Any]:
    """Parse ``server.json`` (MCP Registry metadata)."""
    return cast("dict[str, Any]", json.loads(read_text("server.json")))


def workflow(name: str) -> dict[str, Any]:
    """Parse ``.github/workflows/<name>``."""
    return load_yaml(f".github/workflows/{name}")


def workflow_names() -> list[str]:
    """Return the file names of every workflow."""
    return sorted(path.name for path in WORKFLOWS.glob("*.yml"))


def triggers(parsed: dict[Any, Any]) -> dict[str, Any]:
    """Return a workflow's ``on:`` mapping (YAML 1.1 reads the bare key ``on`` as ``True``)."""
    return cast("dict[str, Any]", parsed[True])


def jobs(parsed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return a workflow's jobs."""
    return cast("dict[str, dict[str, Any]]", parsed["jobs"])


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a job's steps (empty for a job that calls a reusable workflow)."""
    return cast("list[dict[str, Any]]", job.get("steps", []))


def step_using(job: dict[str, Any], action: str) -> dict[str, Any]:
    """Return the only step of ``job`` that uses ``action`` (``owner/name``, any ref)."""
    found = [step for step in steps(job) if step.get("uses", "").startswith(f"{action}@")]
    assert len(found) == 1, f"expected one {action} step, found {len(found)}"
    return found[0]


def step_named(job: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the only step of ``job`` called ``name``."""
    found = [step for step in steps(job) if step.get("name") == name]
    assert len(found) == 1, f"expected one step named {name!r}, found {len(found)}"
    return found[0]


def run_lines(job: dict[str, Any]) -> list[str]:
    """Return every non-blank shell line the job runs, stripped."""
    lines: list[str] = []
    for step in steps(job):
        lines.extend(line.strip() for line in step.get("run", "").splitlines() if line.strip())
    return lines


def all_jobs() -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(workflow file, job id, job)`` for every job of every workflow."""
    return [
        (name, job_id, job)
        for name in workflow_names()
        for job_id, job in jobs(workflow(name)).items()
    ]
