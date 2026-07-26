# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-26

### Added

- Installable package scaffold (`haru` console script, `python -m haru`).
- `haru --version` via Click with version resolved from package metadata.
- Toolchain: uv packaging, ruff lint/format, mypy strict, pytest with a 90% coverage gate.
- Pre-commit hooks, Renovate configuration, GitHub Actions CI (Python 3.13/3.14) and
  PyPI release workflow (Trusted Publishing).
- Steering documents under `.kiro/steering/`.
