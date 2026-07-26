# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `haru doctor`: diagnoses configuration, SSO token, identity provenance,
  role credentials, caller identity, region divergence, Bedrock reachability,
  inference-profile availability, and guardrail sanity, with per-check
  remediation and `--json` output. `--invoke` makes one real (billable)
  Bedrock call for a definitive answer.
- `haru doctor --all-roles`: probes every account and role you are assigned
  and prints a matrix of which combinations can actually reach Bedrock -
  answering "which IAM role do I need?" empirically.
- Global `--debug` flag and real logging: `logging.yaml` (level/format/file)
  is now honoured instead of being dead config. AWS SDK logging rises to INFO
  under `--debug` (operations and error codes, never bodies or headers), and a
  redaction filter masks credential-shaped values.
- `docs/troubleshooting.md`: the IAM policy to hand an AWS administrator,
  including the easily-missed foundation-model ARNs that cross-region
  inference profiles require, and why a Kiro/Amazon Q role will not work.

### Fixed

- Bedrock failures no longer surface as raw botocore tracebacks. AccessDenied,
  ResourceNotFound, Validation, Throttling, and expired-credential errors are
  translated into actionable messages naming the model, region, role, and the
  exact IAM action required. A denial mid-chat now returns to the prompt
  instead of ending the session.

### Fixed

- Stale configuration pins no longer produce a broken sign-in: `haru login`
  validates a pinned `role_name`/`account_id` against your actual Identity
  Center assignments, warns, and falls back to the chooser when they do not
  match (previously a leftover `role_name` from an older `config init` was
  persisted unchecked and every later command failed).
- Role rejections are reported honestly: `AWS rejected role 'X' in account
  'Y' … the role may not be assigned to you` instead of the misleading
  "SSO credentials rejected; run 'haru login'", which is now reserved for
  genuinely invalid tokens.

### Changed

- CLI startup is roughly 6x faster (~0.6s to ~0.1s for `haru --version`):
  strands, mcp, boto3, botocore, and opentelemetry are imported lazily at
  their call sites, so `--help`, `--version`, `config`, `agents`, and
  `session list` never load the agent/AWS stack. A guard test fails the
  build if an eager import creeps back in.
- `haru login` and `haru config show` report where the account and role
  came from (login selection, pinned in config, or an environment variable).

### Added

- The login callback now serves a proper local result page (dark themed,
  approval/denial card): "Request approved" on success, "Request denied"
  with the reason on error, missing parameters, or a state mismatch —
  rendered server-side with no JavaScript.

### Security

- Login result pages ship strict browser security headers: CSP
  (`default-src 'none'`, inline styles only, `frame-ancestors 'none'`),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, and `Cache-Control: no-store`; error
  descriptions are HTML-escaped, and the `state` parameter is now also
  validated in the callback handler before the browser is answered.

### Added

- Browser-only login: `haru login` now discovers your accessible AWS
  accounts and permission-set roles from Identity Center after sign-in
  (auto-selected when unambiguous, prompted otherwise) and remembers the
  choice — no IAM role name or account id env var needed upfront.
  `auth.sso.account_id`/`role_name` remain available as optional pins.

### Changed

- `haru config init` no longer asks for an IAM role name; the
  `HARU_AWS_ACCOUNT_ID` step is gone from the quickstart.

### Added

- Sampling controls (`temperature`, `top_p`, `top_k`, `seed`) across three
  surfaces: model entries, per-agent `sampling:` blocks, and CLI/REPL
  overrides (`--temperature/--top-p/--top-k/--seed`, `/sampling`).
  Precedence CLI/REPL → agent → model, merged per-field; unset fields are
  omitted from requests. `top_k`/`seed` travel via Converse
  `additionalModelRequestFields`.

### Fixed

- `temperature` is no longer required (or sent by default) on model entries —
  Claude 5-series and Opus 4.7+ reject non-default sampling values with an
  HTTP 400, which the previous always-sent temperature would have triggered.

### Added

- `haru config init`: interactive starter configuration under
  `~/.config/haru` (SSO settings prompted; guardrails explicitly disabled
  when no id is provided). `haru config show` prints a secret-free summary.
- Config resolution order: `--config`, `$HARU_CONFIG`, `./config/haru.yaml`,
  `~/.config/haru/haru.yaml` — installed CLIs work outside a project clone.
- `haru agents`: list configured agents with model, prompt, tools, and MCP
  servers.
- Chat REPL slash commands: `/help`, `/model` (list/switch models), `/agent`
  (list/switch agents).

### Fixed

- `haru login` reports configuration errors as clean messages instead of a
  traceback, pointing at `haru config init`.

## [0.1.0] - 2026-07-26

### Added

- `haru login`: IAM Identity Center sign-in via the OAuth 2.0 authorization-code
  flow with PKCE (S256), loopback-only redirect capture, and a
  botocore-compatible token cache (`~/.aws/sso/cache`, 0600) with automatic
  refresh via the `refresh_token` grant.
- `haru chat`: interactive streaming REPL with clean Ctrl-C/Ctrl-D handling,
  `--agent` selection, and `--session-id` persistence; `haru run` for one-shot
  prompts; `haru session list` for stored conversations.
- Typed YAML configuration with includes, `${env:VAR}` interpolation,
  inline-secret rejection, and load-time cross-reference validation.
- Strands BedrockModel factory with `us.` data-residency defaults, streaming
  on, and Bedrock Guardrails attached (input redaction on; enabled guardrails
  without an id fail closed).
- Built-in tool allowlist (strands_tools) and MCP clients for stdio and
  streamable-http transports with disabled/continue-on-error handling.
- Versioned steering prompts under `config/prompts/` with base+overlay
  composition.
- Multi-agent orchestration: supervisor (agents-as-tools), swarm, and graph
  patterns built from configuration.
- Session persistence: project-local file backend (default) and S3 backend.
- OpenTelemetry OTLP tracing via the Strands telemetry helper (no-op when
  disabled).
- Toolchain: uv packaging, ruff lint/format, mypy strict, pytest with a 90%
  coverage gate, pre-commit hooks, Renovate, GitHub Actions CI (Python
  3.13/3.14), and a PyPI release workflow using Trusted Publishing.
