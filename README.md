# haru-cli

A secure, scriptable command line interface for interacting with Amazon Bedrock Claude models
through governed, observable, multi-agent workflows. haru-cli is a thin, functional orchestration
layer over the [AWS Strands Agents SDK](https://strandsagents.com), talking to Bedrock through the
Converse API with streaming, tool use, MCP client support, multi-agent orchestration, session
persistence, OpenTelemetry observability, and Bedrock Guardrails.

## Requirements

- Python 3.13+ (tested on 3.13 and 3.14)
- [uv](https://docs.astral.sh/uv/)
- AWS access via IAM Identity Center (SSO)

## Quickstart

```bash
uv sync
uv run haru --version
```

Or install the published package:

```bash
pipx install haru-cli
haru --version
```

## Development

All quality gates must pass before a change is considered done:

```bash
uv run ruff check
uv run ruff format
uv run mypy src
uv run pytest
```

Install the pre-commit hooks once per clone:

```bash
uv tool run pre-commit install
```

## Project layout

- `src/haru/` — the package (auth, config, models, agents, tools, sessions, observability, commands)
- `config/` — declarative YAML configuration (no secrets; environment references only)
- `tests/unit`, `tests/integration` — pytest suites (coverage gate: 90%)
- `.kiro/steering/` — authoritative engineering steering documents

## License

Apache-2.0. See [LICENSE](LICENSE).
