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

## Install

```bash
pipx install haru-cli
```

Or from a clone:

```bash
uv sync
```

## Commands

| Command                          | Purpose                                                       |
| -------------------------------- | ------------------------------------------------------------- |
| `haru login`                     | Browser sign-in to IAM Identity Center (OAuth 2.0 + PKCE)     |
| `haru chat`                      | Interactive streaming chat REPL                               |
| `haru chat --session-id <id>`    | Persist and restore the conversation under a session id       |
| `haru chat --agent <name>`       | Chat with a specific configured agent                         |
| `haru run "<prompt>"`            | One-shot prompt; prints the answer and exits                  |
| `haru session list`              | List stored session ids                                       |
| `haru --version`                 | Print the installed version                                   |

Every command accepts `--config <path>` (default: `config/haru.yaml`).

## Quickstart

```bash
uv run haru login
uv run haru run "Summarise the Converse API in two sentences."
uv run haru chat --agent supervisor --session-id demo
```

Authentication uses the same PKCE authorization-code flow as `aws sso login`,
caching tokens in the botocore-compatible schema under `~/.aws/sso/cache`
(0600) so standard AWS tooling can consume and refresh the same cache. Access
tokens are refreshed automatically; when a fresh login is needed, commands say
so plainly.

## Configuration

Declarative YAML under `config/` — models, agents, orchestration
(supervisor/swarm/graph), MCP servers, guardrails, sessions, logging, and
OpenTelemetry. No secrets in YAML: values reference environment variables with
`${env:VAR}`. See [docs/configuration.md](docs/configuration.md).

## Development

All quality gates must pass before a change is considered done:

```bash
uv run ruff check
uv run ruff format
uv run mypy src
uv run pytest
```

Coverage is gated at 90% (`--cov-fail-under=90`). CI runs the full gate set on
Python 3.13 and 3.14; releases are built with `uv build` and published to PyPI
via Trusted Publishing on `v*` tags.

Install the pre-commit hooks once per clone:

```bash
uv tool run pre-commit install
```

## Project layout

- `src/haru/` — the package (auth, config, models, agents, tools, steering, sessions, observability, commands)
- `config/` — declarative YAML configuration and steering prompts
- `tests/unit`, `tests/integration` — pytest suites (coverage gate: 90%)
- `.kiro/steering/` — authoritative engineering steering documents

## License

Apache-2.0. See [LICENSE](LICENSE).
