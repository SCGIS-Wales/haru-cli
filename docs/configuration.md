# Configuration

All configuration is declarative YAML under `config/`, loaded from
`config/haru.yaml` by default (override with `--config` on any command).
The base file holds the `app`, `auth`, and optional `sessions` sections and
declares include files that complete the configuration:

| Include file      | Section(s)                  | Contents                                        |
| ----------------- | --------------------------- | ----------------------------------------------- |
| `models.yaml`     | `default_model`, `models`   | Bedrock model catalogue (id, region, sampling)  |
| `agents.yaml`     | `agents`, `orchestration`   | Agent roster, supervisor/swarm/graph wiring     |
| `mcp.yaml`        | `mcp_servers`               | MCP servers (stdio and streamable-http)         |
| `guardrails.yaml` | `guardrails`                | Bedrock Guardrails (id, version, redaction)     |
| `logging.yaml`    | `logging`, `observability`  | Log level/format and OpenTelemetry settings     |

Steering prompts live as markdown files in `config/prompts/`; agents reference
them via `system_prompt_ref`, and a composite reference such as
`base+researcher` concatenates a shared base with a role overlay.

## Environment references

No secrets ever appear in YAML. String values may reference environment
variables with `${env:VAR}` or `${VAR}`; references resolve at load time and
an unset variable fails fast with a `ConfigError`. Keys named `secret`,
`password`, or `token` are rejected outright unless they hold an environment
reference.

## Validation

Every document validates into a frozen, typed schema that rejects unknown
keys. Cross-references are checked at load time: agents must reference
catalogued models and configured MCP servers; swarm members and graph nodes
must reference configured agents.

## Data residency

Bare Bedrock model ids receive the `us.` geographic inference-profile prefix
by default. Explicit prefixes (`eu.`, `au.`, `global.`, ...) and full ARNs
pass through as configured — writing one into YAML is the approval surface.

## Sampling

`temperature`, `top_p`, `top_k`, and `seed` are optional on every model entry
and may also be set per agent (a `sampling:` block) or per invocation
(`--temperature/--top-p/--top-k/--seed`, or `/sampling` inside `haru chat`).
Precedence is CLI/REPL → agent → model, merged per-field; unset fields are
omitted from requests. `temperature`/`top_p` map to the Converse inference
configuration; `top_k` and `seed` travel via
`additionalModelRequestFields`. Claude 5-series and Opus 4.7+ models reject
non-default sampling values (HTTP 400), and no Claude model honours `seed` —
use these fields with models that accept them (for example Haiku 4.5).

## Logging

`logging.yaml` is honoured: `level` (DEBUG/INFO/WARNING/ERROR), `format`
(`json` or `text`), and `file` (a path also writes a 0600 log file). The
global `--debug` flag overrides the level and additionally raises AWS SDK
logging to INFO - operation names, endpoints, retries, and error codes, never
request bodies or headers. A redaction filter masks credential-shaped values
in all log output.

## Sessions

Conversation persistence is configured in the base file:

```yaml
sessions:
  backend: file            # file (default) or s3
  storage_dir: ./.haru/sessions
  # bucket: my-bucket      # required for the s3 backend
  # prefix: haru/sessions
  # region: us-east-1
```

The file backend always uses an explicit project-local directory; the OS
temp directory is never used.
