# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Releases]

## [Unreleased]

### Changed

- Dependabot version updates run monthly instead of weekly.

### Fixed

- CI configuration

### Security

- `opentelemetry-api` 1.45.0 raised to 1.45.1 (CVE-2026-114603) and uv_build 0.13.1 raised to 0.13.6 (GHSA-hq85-5cpj-mr62)
- Updated several other dependencies

## [1.0.1] - 2026-10-12

### Changed

- Dependency updates: uv to 0.13.0, uv_build to 0.13.0, ruff-pre-commit to 0.16.10,
  commitizen to 4.19.1, typos to 5.0.7. The `mcp` floor is now `>=2.3` (2.2.0 differs from 2.3.0
  in the `initialize` response and broke the parity checks).

## [1.0.0] - 2026-10-11

Initial release.
