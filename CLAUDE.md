# CLAUDE.md

This file guides Claude Code when working in this repository. Read it fully before generating or editing code. The steering documents in `.kiro/steering/` are authoritative and expand on the rules here.

## Project

haru-cli is an open, enterprise-grade Python command line interface for a regulated financial services firm. It is a thin, functional orchestration layer over the AWS Strands Agents SDK, talking to Amazon Bedrock Claude models through the Converse API with streaming, tool use, MCP client support, multi-agent orchestration, session persistence, OpenTelemetry observability, and Bedrock Guardrails.

## Golden rules

1. Latest stable Python (target 3.13, test on 3.13 and 3.14). PEP 8 and PEP 257 compliant.
2. Functional style. Prefer pure functions and frozen dataclasses. Do not introduce classes unless a third-party contract requires one. No global mutable state.
3. All configuration lives in YAML under `config/`. No hardcoded values in code. No secrets in YAML: use environment references (`${env:VAR}`) or the AWS credential chain.
4. Use Click for every command. Package as an installable distribution via pyproject.toml.
5. Solid unit test coverage with pytest. New code ships with tests. Keep coverage at or above 90%.
6. Every change goes on a new branch off `main`. Never commit directly to `main`.
7. Do not weaken security controls, logging, or guardrails to make a test pass.

## Toolchain

- Packaging and environments: uv. Lint and format: ruff. Types: mypy (strict). Tests: pytest with pytest-cov.
- Run locally: `uv sync`, `uv run ruff check`, `uv run ruff format`, `uv run mypy src`, `uv run pytest`.

## Dependencies

- strands-agents (>=1.42.0), boto3/botocore (>=1.43.0, always latest), click (>=8.2.0), pyyaml, pydantic, opentelemetry-sdk, rich.
- Keep boto3 current with a lower-bound pin and a committed uv.lock refreshed by Renovate.

## Architecture summary

- `auth/` IAM Identity Center PKCE login and boto3 session construction.
- `config/` YAML loading and typed schema.
- `models/` Strands BedrockModel factories.
- `agents/` agent factories and multi-agent orchestration.
- `tools/` built-in tool registry and MCP clients.
- `sessions/` file and S3 session managers.
- `observability/` OpenTelemetry and Bedrock Guardrails.
- `commands/` Click command implementations.

## What not to do

- Do not reimplement the agent loop, model transport, or tool execution; delegate to Strands.
- Do not hardcode model IDs, regions, ARNs, URLs, or ports; read them from YAML.
- Do not log secrets, tokens, or full prompts containing customer data.
- Do not consider a chunk done until ruff, mypy, and pytest all pass.