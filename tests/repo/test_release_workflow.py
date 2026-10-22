"""The release workflow: gated on CI, PEP 440 aware, signed, and pinned."""

from __future__ import annotations

import re

import pytest
from packaging.version import Version
from repo_files import jobs, run_lines, step_named, step_using, steps, triggers, workflow

RELEASE = workflow("release.yml")
RELEASE_JOBS = jobs(RELEASE)


def _needs(job_id: str) -> set[str]:
    needs = RELEASE_JOBS[job_id].get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _upstream(job_id: str) -> set[str]:
    seen: set[str] = set()
    pending = list(_needs(job_id))
    while pending:
        current = pending.pop()
        if current not in seen:
            seen.add(current)
            pending.extend(_needs(current))
    return seen


def test_ci_workflow_is_reusable() -> None:
    call = triggers(workflow("ci.yml"))["workflow_call"]
    assert call["inputs"]["release"] == {
        "description": "Called from the release workflow (skips the Codecov upload).",
        "type": "boolean",
        "default": False,
    }


def test_release_runs_the_whole_ci_workflow_first() -> None:
    ci = RELEASE_JOBS["ci"]
    assert ci["uses"] == "./.github/workflows/ci.yml"
    assert ci["with"] == {"release": True}
    assert ci["permissions"] == {"contents": "read"}
    assert _needs("build") == {"ci"}
    for job_id in RELEASE_JOBS.keys() - {"ci"}:
        assert "ci" in _upstream(job_id), job_id


def test_codecov_is_skipped_when_called_from_the_release() -> None:
    ci_jobs = jobs(workflow("ci.yml"))
    assert ci_jobs["codecov"]["if"] == "${{ !inputs.release }}"
    upload = step_named(ci_jobs["test"], "Keep the coverage report of one cell for Codecov")
    assert upload["if"].endswith("&& !inputs.release")


def test_ci_artifact_names_do_not_clash_with_the_release_artifact() -> None:
    # A called workflow shares the caller's run, and artifact names are unique per run.
    release_names = {
        step["with"]["name"]
        for job in RELEASE_JOBS.values()
        for step in steps(job)
        if step.get("uses", "").startswith("actions/upload-artifact@")
    }
    ci_names = {
        step["with"]["name"]
        for job in jobs(workflow("ci.yml")).values()
        for step in steps(job)
        if step.get("uses", "").startswith("actions/upload-artifact@")
    }
    assert release_names == {"dist"}
    assert not release_names & ci_names


def test_tagged_commit_must_be_on_main() -> None:
    build = RELEASE_JOBS["build"]
    assert steps(build)[0]["with"]["fetch-depth"] == 0
    check = step_named(build, "Check the tagged commit is on main")
    assert 'git merge-base --is-ancestor "${TAG_SHA}" origin/main' in check["run"]


def _prerelease_pattern() -> str:
    step = step_named(RELEASE_JOBS["build"], "Check tag, pyproject version and server.json agree")
    match = re.search(r"^prerelease_re='([^']+)'$", step["run"], re.MULTILINE)
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize(
    "version",
    [
        "0.1.0",
        "1.0.0",
        "10.20.30",
        "0.2.0a0",
        "0.2.0b3",
        "0.2.0rc0",
        "0.2.0rc12",
        "0.2.0.dev1",
        "1.0.0.post1",
        "1.0.0.post1.dev2",
        "2.0.0rc1.post1",
    ],
)
def test_prerelease_detection_matches_pep_440(version: str) -> None:
    detected = re.search(_prerelease_pattern(), version) is not None
    assert detected == Version(version).is_prerelease


def test_build_exports_version_and_prerelease() -> None:
    build = RELEASE_JOBS["build"]
    assert build["outputs"] == {
        "version": "${{ steps.version.outputs.version }}",
        "prerelease": "${{ steps.version.outputs.prerelease }}",
    }
    lines = run_lines(build)
    assert 'echo "version=${version}" >> "${GITHUB_OUTPUT}"' in lines
    assert 'echo "prerelease=${prerelease}" >> "${GITHUB_OUTPUT}"' in lines


def test_container_tags_follow_pep_440_and_skip_latest_for_prereleases() -> None:
    meta = step_using(RELEASE_JOBS["publish-docker"], "docker/metadata-action")
    tags = meta["with"]["tags"].split()
    # semver cannot parse `v0.2.0rc0`, and an explicit `type=raw,value=latest` would tag an RC.
    assert tags == ["type=pep440,pattern={{version}}", "type=pep440,pattern={{major}}.{{minor}}"]
    assert meta["with"]["flavor"] == "latest=auto"


def test_container_image_is_multi_platform() -> None:
    job = RELEASE_JOBS["publish-docker"]
    step_using(job, "docker/setup-qemu-action")
    push = step_using(job, "docker/build-push-action")
    assert push["with"]["platforms"] == "linux/amd64,linux/arm64"


def test_github_release_marks_prereleases() -> None:
    job = RELEASE_JOBS["github-release"]
    assert "build" in _needs("github-release")
    step = steps(job)[-1]
    assert step["env"]["PRERELEASE"] == "${{ needs.build.outputs.prerelease }}"
    lines = run_lines(job)
    assert 'if [ "${PRERELEASE}" = "true" ]; then flags+=(--prerelease); fi' in lines
    assert lines[-1] == '--title "${TAG}" "${flags[@]}"'


def test_prereleases_are_not_published_to_the_mcp_registry() -> None:
    job = RELEASE_JOBS["publish-mcp-registry"]
    assert job["if"] == "needs.build.outputs.prerelease != 'true'"


def test_mcp_publisher_is_checked_against_a_pinned_digest() -> None:
    job = RELEASE_JOBS["publish-mcp-registry"]
    install = step_named(job, "Install mcp-publisher (pinned version and checksum)")
    assert re.fullmatch(r"v\d+\.\d+\.\d+", install["env"]["PUBLISHER_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", install["env"]["PUBLISHER_SHA256"])
    assert (
        'echo "${PUBLISHER_SHA256}  mcp-publisher_linux_amd64.tar.gz" | sha256sum --check -'
        in install["run"]
    )
    # The checksum file of the same release proves nothing about a replaced asset.
    assert "checksums" not in install["run"]


def test_mcp_registry_publish_waits_for_pypi() -> None:
    job = RELEASE_JOBS["publish-mcp-registry"]
    names = [step.get("name", step.get("run")) for step in steps(job)]
    wait = names.index("Wait until PyPI serves the new version")
    assert names.index("./mcp-publisher publish") > wait
    step = step_named(job, "Wait until PyPI serves the new version")
    assert step["env"]["VERSION"] == "${{ needs.build.outputs.version }}"
    assert '"https://pypi.org/pypi/staan-mcp/${VERSION}/json"; then' in step["run"]
    assert "for attempt in $(seq 1 20); do" in step["run"]
    assert step["run"].rstrip().endswith("exit 1")


def test_sbom_is_attested_against_the_distributions() -> None:
    build_lines = " ".join(run_lines(RELEASE_JOBS["build"]))
    assert "--output-file dist-sbom.cdx.json" in build_lines
    attest = step_using(RELEASE_JOBS["attest"], "actions/attest-sbom")
    assert attest["with"] == {"subject-path": "dist/*", "sbom-path": "dist-sbom.cdx.json"}
    assert re.fullmatch(r"actions/attest-sbom@[0-9a-f]{40}", attest["uses"])
    assert RELEASE_JOBS["attest"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }


def test_the_stdio_bundle_is_built_checked_attested_and_released() -> None:
    build = RELEASE_JOBS["build"]
    # Built where no token exists, from the standard library alone (no project environment).
    assert build["permissions"] == {"contents": "read"}
    lines = run_lines(build)
    command = "uv run --no-project python scripts/build_zipapp.py"
    assert f"{command} dist-bundle/staan-mcp-stdio.pyz" in lines
    # A second build must be byte-identical: the release asset is reproducible.
    again = next(line for line in lines if line.startswith(f"{command} ") and "again" in line)
    assert any(
        line.startswith("cmp dist-bundle/staan-mcp-stdio.pyz ") and again.split()[-1] in line
        for line in lines
    )
    # It is run on a bare interpreter, away from the checkout, before anything is uploaded.
    smoke = step_named(build, "Smoke-test the bundle on a bare interpreter")
    assert 'python -I -S "${bundle}"' in smoke["run"]
    assert smoke["run"].count('cd "${RUNNER_TEMP}"') == 1
    names = [step.get("name") or step.get("uses", "") for step in steps(build)]
    upload = next(i for i, name in enumerate(names) if name.startswith("actions/upload-artifact@"))
    assert names.index(smoke["name"]) < upload
    assert step_using(build, "actions/upload-artifact")["with"]["path"].split() == [
        "dist/",
        "dist-bundle/",
        "dist-sbom.cdx.json",
    ]

    # Signed provenance covers the bundle too. The SBOM lists the locked runtime dependencies
    # of the wheel and sdist, which the dependency-free bundle does not contain.
    attest = RELEASE_JOBS["attest"]
    provenance = step_using(attest, "actions/attest-build-provenance")
    assert provenance["with"]["subject-path"].split() == ["dist/*", "dist-bundle/*"]
    assert step_using(attest, "actions/attest-sbom")["with"]["subject-path"] == "dist/*"

    # PyPI would reject a .pyz: it gets the `dist/` directory only. The GitHub release lists it.
    publish = step_using(RELEASE_JOBS["publish-pypi"], "pypa/gh-action-pypi-publish")
    assert publish["with"]["packages-dir"] == "dist/"
    assert any(line.startswith("uv run --only-group dist twine check") for line in lines)
    assert all("dist-bundle" not in line for line in lines if "twine" in line)
    create = " ".join(run_lines(RELEASE_JOBS["github-release"]))
    assert 'gh release create "${TAG}" dist/* dist-bundle/* dist-sbom.cdx.json ' in create
