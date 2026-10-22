"""Dockerfile, Dependabot, pre-commit and commitizen settings that keep releases correct."""

from __future__ import annotations

import re

import pytest
from repo_files import load_yaml, pyproject, read_text

# dependabot-core docker/lib/dependabot/docker/file_parser.rb: FROM [--platform] [registry/]image
# [:tag][@sha256:digest]. A tag that is not `[\w][\w.-]{0,127}` (for example `${UV_VERSION}`)
# leaves tag and digest unmatched (the pattern is not anchored at the end), and Dependabot
# silently skips the line.
DEPENDABOT_FROM = re.compile(
    r"^FROM\s+(?:--platform=\S+\s+)?"
    r"(?:(?P<registry>[\w-]+(?:\.[\w-]+)+(?::\d+)?)/)?"
    r"(?P<image>[a-z0-9]+(?:[._-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)*)"
    r"(?::(?P<tag>[\w][\w.-]{0,127}))?"
    r"(?:@sha256:(?P<digest>[0-9a-f]{64}))?"
    r"(?:\s+AS\s+(?P<stage>[\w-]+))?",
    re.IGNORECASE,
)
CONVENTIONAL_TYPES = {"build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "test"}


def _from_lines() -> list[str]:
    return [line for line in read_text("Dockerfile").splitlines() if line.startswith("FROM ")]


def test_dockerfile_has_no_build_args_in_base_images() -> None:
    dockerfile = read_text("Dockerfile")
    assert not re.search(r"^ARG ", dockerfile, re.MULTILINE)
    assert "${" not in "\n".join(_from_lines())


@pytest.mark.parametrize("line", _from_lines())
def test_dependabot_can_parse_every_base_image(line: str) -> None:
    match = DEPENDABOT_FROM.match(line)
    assert match is not None, line
    assert match.group("tag"), line
    assert match.group("digest"), line
    assert match.group("stage") in {"uv", "builder", "runtime"}, line


def test_dependabot_regex_rejects_an_interpolated_tag() -> None:
    match = DEPENDABOT_FROM.match(
        "FROM ghcr.io/astral-sh/uv:${UV_VERSION}@sha256:" + "a" * 64 + " AS uv"
    )
    assert match is not None
    assert match.group("image") == "astral-sh/uv"
    assert match.group("tag") is None
    assert match.group("digest") is None


def test_runtime_image_uses_a_supported_debian_and_read_only_packages() -> None:
    lines = _from_lines()
    # Dependabot bumps the minor and patch versions on its own (test_workflow_hygiene ties the uv
    # tag to uv_build), so only the major versions, the images and the Debian flavour are pinned.
    tags = [re.sub(r":(\d+)(\.\d+)+", r":\1.X", line.split("@", 1)[0]) for line in lines]
    assert tags == [
        "FROM ghcr.io/astral-sh/uv:0.X",
        "FROM python:3.X-slim-trixie",
        "FROM python:3.X-slim-trixie",
    ]
    dockerfile = read_text("Dockerfile")
    assert "COPY --from=builder /app/.venv /app/.venv\n" in dockerfile
    assert "--chown" not in dockerfile


def test_dependabot_commits_are_conventional() -> None:
    config = load_yaml(".github/dependabot.yml")
    ecosystems = {entry["package-ecosystem"] for entry in config["updates"]}
    assert ecosystems == {"uv", "github-actions", "docker", "pre-commit"}
    for entry in config["updates"]:
        message = entry["commit-message"]
        assert message["prefix"] in CONVENTIONAL_TYPES, entry
        assert message["include"] == "scope", entry


def test_pre_commit_ci_is_not_configured() -> None:
    # pre-commit.ci's autoupdate rewrites `rev:` to a tag and races Dependabot.
    config = load_yaml(".pre-commit-config.yaml")
    assert "ci" not in config


def test_every_hook_is_pinned_by_sha() -> None:
    config = load_yaml(".pre-commit-config.yaml")
    hooks = read_text(".pre-commit-config.yaml")
    remote = [repo for repo in config["repos"] if repo["repo"] != "local"]
    assert remote
    for repo in remote:
        assert re.fullmatch(r"[0-9a-f]{40}", repo["rev"]), repo["repo"]
        assert re.search(rf"rev: {repo['rev']} # frozen: \S+\n", hooks), repo["repo"]


def test_ruff_hook_shows_its_fixes() -> None:
    config = load_yaml(".pre-commit-config.yaml")
    ruff_check = [
        hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "ruff-check"
    ]
    assert ruff_check[0]["args"] == ["--fix", "--show-fixes", "--exit-non-zero-on-fix"]


def test_ruff_target_version_comes_from_requires_python() -> None:
    assert "target-version" not in pyproject()["tool"]["ruff"]
    assert pyproject()["project"]["requires-python"] == ">=3.11"


def test_cz_bump_leaves_the_hand_written_changelog_alone() -> None:
    commitizen = pyproject()["tool"]["commitizen"]
    # Commitizen's generator does not understand Keep a Changelog headings and deletes them.
    assert commitizen["update_changelog_on_bump"] is False
    assert "changelog_incremental" not in commitizen
    assert commitizen["version_provider"] == "uv"
    assert commitizen["version_files"] == [
        "server.json:version",
        "src/staan_mcp/stdio_server.py:^VERSION: ",
        "src/hermes/plugin.yaml:^version: ",
    ]
    assert commitizen["tag_format"] == "v$version"
