# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Releases]

## [Unreleased]

### Fixed

- README Python-versions badge showed shields.io's "package or version not found" after the first
  PyPI release: GitHub's image proxy had cached the pre-publish render under its URL for 24 h and
  kept serving it. The two PyPI badge URLs now carry an inert cache-busting query.

## [1.0.1] - 2026-10-12

### Changed

- Dependency updates: uv to 0.13.0, uv_build to 0.13.0, ruff-pre-commit to 0.16.10,
  commitizen to 4.19.1, typos to 5.0.7. The `mcp` floor is now `>=2.3` (2.2.0 differs from 2.3.0
  in the `initialize` response and broke the parity checks).

## [1.0.0] - 2026-10-11

Initial release.
