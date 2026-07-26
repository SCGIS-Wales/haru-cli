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
| `haru doctor`                    | Diagnose config, sign-in, and Bedrock permissions              |
| `haru doctor --all-roles`        | Find which account+role can actually reach Bedrock            |
| `haru config init`               | Create a starter configuration (interactive)                  |
| `haru config show`               | Show the resolved configuration (no secrets)                  |
| `haru login`                     | Browser sign-in to IAM Identity Center (OAuth 2.0 + PKCE)     |
| `haru chat`                      | Interactive streaming chat REPL                               |
| `haru chat --session-id <id>`    | Persist and restore the conversation under a session id       |
| `haru chat --agent <name>`       | Chat with a specific configured agent                         |
| `haru run "<prompt>"`            | One-shot prompt; prints the answer and exits                  |
| `haru agents`                    | List configured agents (model, prompt, tools, MCP)            |
| `haru session list`              | List stored session ids                                       |
| `haru --version`                 | Print the installed version                                   |

Configuration is resolved from `--config`, then `$HARU_CONFIG`, then
`./config/haru.yaml`, then `~/.config/haru/haru.yaml`. `haru --debug <command>`
turns on verbose logging, including the AWS API operations and error codes
behind a failure (never request bodies or credentials).

Inside `haru chat`, slash commands switch targets mid-session:

| REPL command     | Purpose                                                    |
| ---------------- | ---------------------------------------------------------- |
| `/help`          | Show REPL commands                                         |
| `/model`         | List configured models (default and active marked)         |
| `/model <name>`  | Switch the default agent to that model (resets the chat)   |
| `/agent`         | List configured agents                                     |
| `/agent <name>`  | Switch to that agent (resets the chat)                     |

## Quickstart

```bash
haru config init      # writes ~/.config/haru (asks for your SSO start URL)
haru login            # browser sign-in; account and role discovered for you
haru run "Summarise the Converse API in two sentences."
haru chat --agent supervisor --session-id demo
```

`haru login` is browser-only: after Identity Center sign-in, your accessible
AWS accounts and permission-set roles are discovered automatically (like
`aws configure sso`), auto-selected when there is exactly one, prompted
otherwise, and remembered for later sessions. Pin `auth.sso.account_id` /
`role_name` in configuration only if you want to skip the discovery — pins
are validated against your actual assignments, and a stale pin falls back to
the chooser with a warning rather than failing later.

A pin that sign-in proves is not assigned to you stops being applied by later
commands too, so the fallback actually sticks; haru names the offending line
and never edits your configuration. Editing or removing the pin gives it a
fresh chance on the next login. Full precedence per field: the config pin
(`account_id`, then `account_id_env`, then `role_name`) unless the last
sign-in disproved that exact value, then the identity chosen at login.

The consent screen you see in the browser ("Allow haru-cli to access your
data?") is rendered by AWS IAM Identity Center, and its wording reflects the
scopes the client requests. haru requests only `sso:account:access`, so AWS
describes exactly that: the ability to assume roles you have been assigned.
Once you approve, haru serves a local result page confirming the outcome.

Authentication uses the same PKCE authorization-code flow as `aws sso login`,
caching tokens in the botocore-compatible schema under `~/.aws/sso/cache`
(0600) so standard AWS tooling can consume and refresh the same cache. Access
tokens are refreshed automatically; when a fresh login is needed, commands say
so plainly.

## Sampling controls

haru-cli exposes `temperature`, `top_p`, `top_k`, and `seed` on **three
configuration surfaces** — a capability Kiro CLI does not offer on any of its
configuration surfaces (its v2/v3 agent formats and `cli.json` define no
sampling fields):

| Surface                  | Where                                          | Example                                   |
| ------------------------ | ---------------------------------------------- | ----------------------------------------- |
| Model catalogue          | `models.yaml`, per model entry                 | `temperature: 0.2`, `top_k: 50`           |
| Agent profile            | `agents.yaml`, per agent `sampling:` block     | `sampling: {temperature: 0.1}`            |
| CLI / REPL               | `--temperature/--top-p/--top-k/--seed` flags on `run` and `chat`; `/sampling` in the REPL | `haru run "..." --top-k 20` |

Precedence: **CLI/REPL override → agent `sampling:` block → model entry**,
merged per-field. Unset fields are omitted from requests entirely, keeping
the provider's defaults. In the chat REPL, `/sampling` shows the model
default, session override, and effective value for each field;
`/sampling temperature=0.2 top_k=50` applies overrides and `/sampling reset`
clears them.

Model compatibility (AWS-side behaviour, not a haru limitation):

- **Claude 5-series and Opus 4.7+** (Sonnet 5, Opus 5, Opus 4.8) reject
  non-default `temperature`/`top_p`/`top_k` with an HTTP 400 — leave them
  unset on those entries (the shipped config does).
- **Claude Haiku 4.5, Sonnet 4.5, and older Claude models**, plus
  non-Anthropic Bedrock models, accept them.
- `seed` is forwarded via Converse `additionalModelRequestFields` for Bedrock
  models that support it; **no Claude model accepts a seed today**, so do not
  expect determinism from Claude — the field exists for models that honour it.

## Configuration

Declarative YAML under `config/` — models, agents, orchestration
(supervisor/swarm/graph), MCP servers, guardrails, sessions, sampling,
logging, and OpenTelemetry. No secrets in YAML: values reference environment
variables with `${env:VAR}`. See [docs/configuration.md](docs/configuration.md).

## Troubleshooting

If `haru login` succeeds but `haru chat` fails, the role you signed in with
almost certainly lacks Bedrock permissions. Signing in and being allowed to
call Bedrock are separate things: haru assumes a role in **your** AWS account
and calls Bedrock there, so that role needs `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream`.

Kiro and Amazon Q working is not evidence that haru will — they send your SSO
token to the managed Amazon Q service and never call Bedrock in your account,
so there is no Kiro role to copy.

```bash
haru doctor              # what is configured, signed in, and permitted
haru doctor --all-roles  # which of your assigned roles can reach Bedrock
haru doctor --invoke     # definitive check (makes one billable call)
```

See [docs/troubleshooting.md](docs/troubleshooting.md) for the IAM policy to
hand your AWS administrator.

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
