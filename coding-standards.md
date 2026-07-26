---
inclusion: always
---

# Coding standards

- Functional first: pure functions, frozen dataclasses, explicit inputs and outputs. Avoid classes unless a library contract requires one.
- Full type annotations on every function; mypy strict must pass with no ignores except where narrowly justified and commented.
- No hardcoding: model IDs, regions, ARNs, URLs, ports, and file paths come from YAML config.
- Configuration is validated into typed schema objects at load time; fail fast with `ConfigError` carrying the offending path.
- Errors: raise typed exceptions from `errors.py`; never swallow exceptions silently; convert third-party errors at module boundaries.
- Logging via the standard logging module configured from YAML; structured JSON in production; never log secrets, tokens, or customer data.
- Docstrings on public functions (PEP 257). Keep functions small and composable.
- Line length 100. Format with ruff. No dead code.