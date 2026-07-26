---
inclusion: always
---

# Technology stack

- Language: Python 3.13 (floor), tested on 3.13 and 3.14. PEP 8 and PEP 257.
- CLI: Click 8.2+.
- Agents: AWS Strands Agents SDK 1.42+ with the native BedrockModel provider (Converse/ConverseStream, streaming on).
- AWS: boto3/botocore 1.43+ (always latest), sso-oidc for PKCE login, bedrock-runtime for inference.
- Packaging: uv with pyproject.toml (hatchling backend) and a committed uv.lock.
- Quality: ruff (lint and format), mypy strict, pytest with pytest-cov (>=90%).
- Observability: OpenTelemetry SDK and OTLP exporter via Strands telemetry.
- Config: PyYAML, validated into typed models.

Prefer these tools over alternatives. Do not add a dependency without a clear need; pin lower bounds and rely on uv.lock for reproducibility.