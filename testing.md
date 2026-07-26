---
inclusion: always
---

# Testing

- Framework: pytest. Coverage: pytest-cov with a 90% floor (`--cov-fail-under=90`), branch coverage on.
- Every module has a matching `tests/unit/test_<module>.py`. New code ships with tests in the same change.
- Mock all network and AWS calls: use pytest-mock and moto; never hit real Bedrock, sso-oidc, or S3 in unit tests.
- Test the CLI with `click.testing.CliRunner`, asserting exit codes and output.
- Cover happy paths, error paths, and edge cases (missing config, expired tokens, disabled MCP servers, guardrail interventions).
- Deterministic tests only: no sleeps, no real time dependence, no external services. Use fixtures and `tmp_path`.
- Integration tests live in `tests/integration` and are marked so they can be skipped in fast CI.