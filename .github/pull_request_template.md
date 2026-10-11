## Summary

<!-- What does this change, and why? Link the issue it closes: "Closes #123". -->

## Type of change

<!-- Match your Conventional Commit type. -->

- [ ] fix: bug fix
- [ ] feat: new feature
- [ ] docs, test, refactor, ci, build or chore
- [ ] Breaking change (the commit has `!` or a `BREAKING CHANGE:` footer)

## Checklist

- [ ] The PR title follows [Conventional Commits](https://www.conventionalcommits.org/).
- [ ] `uv run --frozen ruff check .` and `uv run --frozen ruff format --check .` pass.
- [ ] `uv run --frozen mypy` and `uv run --frozen pylint src tests` pass.
- [ ] `uv run --frozen xenon --max-absolute B --max-modules A --max-average A src` passes.
- [ ] `uv run --frozen pytest` passes with 100% line and branch coverage.
- [ ] Tests assert behaviour (they would fail if the code were wrong), not just execute lines.
- [ ] `CHANGELOG.md` has an entry under "Unreleased" for user-visible changes.
- [ ] The README is updated if tool parameters, output shapes, flags or env vars changed.
- [ ] No secrets, API keys or tokens appear in code, tests, fixtures or logs.
