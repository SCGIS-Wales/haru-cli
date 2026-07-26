"""Tests for haru.config: YAML loading, includes, env interpolation, secrets."""

from pathlib import Path

import pytest

from haru.config import HaruConfig, load_config, resolve_env
from haru.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]

BASE_YAML = """\
app:
  name: haru
  default_command: chat
auth:
  sso:
    start_url: https://example.awsapps.com/start
    sso_region: us-east-1
    account_id_env: HARU_AWS_ACCOUNT_ID
    role_name: HaruBedrockInvoke
  bedrock_region: us-east-1
"""

INCLUDES_YAML = """\
includes:
  models: models.yaml
  agents: agents.yaml
  mcp: mcp.yaml
  guardrails: guardrails.yaml
  logging: logging.yaml
"""

MODELS_YAML = """\
default_model: sonnet-5
models:
  sonnet-5:
    model_id: us.anthropic.claude-sonnet-5
    region: us-east-1
    max_tokens: 8192
    temperature: 0.3
  opus-5:
    model_id: us.anthropic.claude-opus-5
    region: us-east-1
    max_tokens: 8192
    temperature: 0.3
    streaming: false
"""

AGENTS_YAML = """\
agents:
  supervisor:
    model: opus-5
    system_prompt_ref: supervisor
    tools: [calculator]
    mcp_servers: [aws-docs]
  writer:
    model: sonnet-5
orchestration:
  default_pattern: graph
  swarm:
    members: [writer]
  graph:
    nodes:
      - id: write
        agent: writer
    edges:
      - from: write
        to: write
"""

MCP_YAML = """\
mcp_servers:
  aws-docs:
    transport: stdio
    command: uvx
    args: ["awslabs.aws-documentation-mcp-server@latest"]
    continue_on_error: true
  internal-api:
    transport: streamable-http
    url: https://mcp.internal.example.com/mcp
    headers:
      authorization: ${env:TEST_MCP_TOKEN}
    disabled: true
"""

GUARDRAILS_YAML = """\
guardrails:
  enabled: true
  guardrail_id: ${env:TEST_GUARDRAIL_ID}
"""

LOGGING_YAML = """\
logging:
  level: INFO
  format: json
observability:
  otel:
    enabled: true
    endpoint: ${env:TEST_OTEL_ENDPOINT}
"""


def write_config_tree(root: Path, *, base: str = BASE_YAML + INCLUDES_YAML) -> Path:
    """Write a complete config tree under ``root`` and return the base path."""
    (root / "haru.yaml").write_text(base, encoding="utf-8")
    (root / "models.yaml").write_text(MODELS_YAML, encoding="utf-8")
    (root / "agents.yaml").write_text(AGENTS_YAML, encoding="utf-8")
    (root / "mcp.yaml").write_text(MCP_YAML, encoding="utf-8")
    (root / "guardrails.yaml").write_text(GUARDRAILS_YAML, encoding="utf-8")
    (root / "logging.yaml").write_text(LOGGING_YAML, encoding="utf-8")
    return root / "haru.yaml"


@pytest.fixture
def config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set every environment variable the fixture config tree references."""
    monkeypatch.setenv("TEST_MCP_TOKEN", "resolved-token")
    monkeypatch.setenv("TEST_GUARDRAIL_ID", "gr-123")
    monkeypatch.setenv("TEST_OTEL_ENDPOINT", "https://otel.example.com:4317")


@pytest.mark.usefixtures("config_env")
def test_happy_path(tmp_path: Path) -> None:
    """A valid config tree parses into fully-typed, resolved objects."""
    config = load_config(write_config_tree(tmp_path))

    assert isinstance(config, HaruConfig)
    assert config.app.name == "haru"
    assert config.auth.sso.callback_port == 0
    assert config.models is not None
    assert config.models.models["sonnet-5"].model_id == "us.anthropic.claude-sonnet-5"
    assert config.models.models["sonnet-5"].streaming is True
    assert config.models.models["opus-5"].streaming is False
    assert config.agents is not None
    assert config.agents.agents["supervisor"].tools == ("calculator",)
    assert config.agents.orchestration is not None
    assert config.agents.orchestration.graph is not None
    assert config.agents.orchestration.graph.edges[0].source == "write"
    assert config.mcp is not None
    assert config.mcp.mcp_servers["internal-api"].headers["authorization"] == "resolved-token"
    assert config.guardrails is not None
    assert config.guardrails.guardrail_id == "gr-123"
    assert config.observability is not None
    assert config.observability.otel.endpoint == "https://otel.example.com:4317"


def test_base_only_config(tmp_path: Path) -> None:
    """A base file without includes loads on its own."""
    path = tmp_path / "haru.yaml"
    path.write_text(BASE_YAML, encoding="utf-8")
    config = load_config(path)
    assert config.includes is None
    assert config.models is None


@pytest.mark.usefixtures("config_env")
def test_default_path_used_when_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_config(None)`` reads config/haru.yaml relative to the CWD."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config_tree(config_dir)
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.app.name == "haru"


def test_missing_config_file(tmp_path: Path) -> None:
    """A missing base file raises ConfigError naming the path."""
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match=r"nope\.yaml"):
        load_config(missing)


def test_missing_include(tmp_path: Path) -> None:
    """A missing include raises ConfigError naming the include path."""
    path = tmp_path / "haru.yaml"
    path.write_text(BASE_YAML + "includes:\n  models: absent.yaml\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"absent\.yaml"):
        load_config(path)


def test_malformed_yaml(tmp_path: Path) -> None:
    """Unparseable YAML raises ConfigError."""
    path = tmp_path / "haru.yaml"
    path.write_text("app: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="Malformed YAML"):
        load_config(path)


def test_non_mapping_yaml(tmp_path: Path) -> None:
    """A YAML document that is not a mapping raises ConfigError."""
    path = tmp_path / "haru.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(path)


def test_unknown_key_rejected(tmp_path: Path) -> None:
    """Unknown keys fail validation with ConfigError."""
    path = tmp_path / "haru.yaml"
    path.write_text(BASE_YAML + "surprise: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="surprise"):
        load_config(path)


def test_env_interpolation_both_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both ${env:VAR} and ${VAR} forms resolve, including inside strings."""
    monkeypatch.setenv("HARU_A", "alpha")
    monkeypatch.setenv("HARU_B", "beta")
    assert resolve_env("${env:HARU_A}") == "alpha"
    assert resolve_env("${HARU_B}") == "beta"
    assert resolve_env("pre-${env:HARU_A}-mid-${HARU_B}-post") == "pre-alpha-mid-beta-post"
    assert resolve_env("no references here") == "no references here"


def test_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset environment reference raises ConfigError naming the variable."""
    monkeypatch.delenv("HARU_ABSENT", raising=False)
    with pytest.raises(ConfigError, match="HARU_ABSENT"):
        resolve_env("${env:HARU_ABSENT}")


def test_missing_env_var_in_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading a file with an unset environment reference fails fast."""
    monkeypatch.delenv("HARU_ABSENT", raising=False)
    path = tmp_path / "haru.yaml"
    path.write_text(BASE_YAML.replace("us-east-1", "${env:HARU_ABSENT}", 1), encoding="utf-8")
    with pytest.raises(ConfigError, match="HARU_ABSENT"):
        load_config(path)


@pytest.mark.parametrize("key", ["token", "secret", "password", "TOKEN"])
def test_inline_secret_rejected(tmp_path: Path, key: str) -> None:
    """Secret-named keys holding literal values are rejected."""
    path = tmp_path / "haru.yaml"
    path.write_text(BASE_YAML + f"extra_settings:\n  {key}: hunter2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Inline secret"):
        load_config(path)


def test_env_reference_secret_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Secret-named keys holding environment references are accepted."""
    monkeypatch.setenv("TEST_MCP_TOKEN", "resolved-token")
    path = tmp_path / "haru.yaml"
    mcp_inline = (
        "mcp:\n"
        "  mcp_servers:\n"
        "    internal:\n"
        "      transport: streamable-http\n"
        "      url: https://mcp.example.com/mcp\n"
        "      headers:\n"
        "        token: ${env:TEST_MCP_TOKEN}\n"
    )
    path.write_text(BASE_YAML + mcp_inline, encoding="utf-8")
    config = load_config(path)
    assert config.mcp is not None
    assert config.mcp.mcp_servers["internal"].headers["token"] == "resolved-token"


def test_inline_secret_in_list_rejected(tmp_path: Path) -> None:
    """Secret keys nested inside lists are also rejected."""
    path = tmp_path / "haru.yaml"
    path.write_text(
        BASE_YAML + "extra_settings:\n  entries:\n    - password: hunter2\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"entries\[0\].password"):
        load_config(path)


def test_stdio_server_requires_command(tmp_path: Path) -> None:
    """A stdio MCP server without a command fails validation."""
    path = tmp_path / "haru.yaml"
    path.write_text(
        BASE_YAML + "mcp:\n  mcp_servers:\n    broken:\n      transport: stdio\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="require a 'command'"):
        load_config(path)


def test_http_server_requires_url(tmp_path: Path) -> None:
    """A streamable-http MCP server without a url fails validation."""
    path = tmp_path / "haru.yaml"
    path.write_text(
        BASE_YAML + "mcp:\n  mcp_servers:\n    broken:\n      transport: streamable-http\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="require a 'url'"):
        load_config(path)


def test_default_model_must_exist(tmp_path: Path) -> None:
    """default_model must name a configured model."""
    path = tmp_path / "haru.yaml"
    models_inline = (
        "models:\n"
        "  default_model: ghost\n"
        "  models:\n"
        "    real:\n"
        "      model_id: us.anthropic.claude-sonnet-5\n"
        "      region: us-east-1\n"
        "      max_tokens: 1024\n"
        "      temperature: 0.2\n"
    )
    path.write_text(BASE_YAML + models_inline, encoding="utf-8")
    with pytest.raises(ConfigError, match="ghost"):
        load_config(path)


def test_agent_model_cross_reference(tmp_path: Path) -> None:
    """An agent referencing a model absent from the catalogue fails."""
    base = BASE_YAML + "includes:\n  models: models.yaml\n  agents: agents.yaml\n"
    (tmp_path / "haru.yaml").write_text(base, encoding="utf-8")
    (tmp_path / "models.yaml").write_text(MODELS_YAML, encoding="utf-8")
    (tmp_path / "agents.yaml").write_text(
        "agents:\n  loner:\n    model: not-a-model\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="not-a-model"):
        load_config(tmp_path / "haru.yaml")


def test_agent_mcp_server_cross_reference(tmp_path: Path) -> None:
    """An agent referencing an unconfigured MCP server fails at load."""
    base = BASE_YAML + "includes:\n  agents: agents.yaml\n  mcp: mcp.yaml\n"
    (tmp_path / "haru.yaml").write_text(base, encoding="utf-8")
    (tmp_path / "agents.yaml").write_text(
        "agents:\n  lonely:\n    model: sonnet-5\n    mcp_servers: [phantom-server]\n",
        encoding="utf-8",
    )
    (tmp_path / "mcp.yaml").write_text("mcp_servers: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="phantom-server"):
        load_config(tmp_path / "haru.yaml")


def test_swarm_member_must_be_agent(tmp_path: Path) -> None:
    """Swarm members must be configured agents."""
    path = tmp_path / "haru.yaml"
    agents_inline = (
        "agents:\n"
        "  agents:\n"
        "    writer:\n"
        "      model: sonnet-5\n"
        "  orchestration:\n"
        "    swarm:\n"
        "      members: [phantom]\n"
    )
    path.write_text(BASE_YAML + agents_inline, encoding="utf-8")
    with pytest.raises(ConfigError, match="phantom"):
        load_config(path)


def test_graph_edge_must_reference_node(tmp_path: Path) -> None:
    """Graph edges must reference declared nodes."""
    path = tmp_path / "haru.yaml"
    agents_inline = (
        "agents:\n"
        "  agents:\n"
        "    writer:\n"
        "      model: sonnet-5\n"
        "  orchestration:\n"
        "    graph:\n"
        "      nodes:\n"
        "        - id: write\n"
        "          agent: writer\n"
        "      edges:\n"
        "        - from: write\n"
        "          to: nowhere\n"
    )
    path.write_text(BASE_YAML + agents_inline, encoding="utf-8")
    with pytest.raises(ConfigError, match="nowhere"):
        load_config(path)


def test_graph_node_must_reference_agent(tmp_path: Path) -> None:
    """Graph nodes must reference configured agents."""
    path = tmp_path / "haru.yaml"
    agents_inline = (
        "agents:\n"
        "  agents:\n"
        "    writer:\n"
        "      model: sonnet-5\n"
        "  orchestration:\n"
        "    graph:\n"
        "      nodes:\n"
        "        - id: rogue\n"
        "          agent: ghostwriter\n"
        "      edges: []\n"
    )
    path.write_text(BASE_YAML + agents_inline, encoding="utf-8")
    with pytest.raises(ConfigError, match="rogue"):
        load_config(path)


def test_repo_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The checked-in config/ tree stays loadable end to end."""
    monkeypatch.setenv("HARU_INTERNAL_MCP_TOKEN", "repo-token")
    monkeypatch.setenv("HARU_GUARDRAIL_ID", "repo-guardrail")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example.com:4317")
    config = load_config(REPO_ROOT / "config" / "haru.yaml")
    assert config.models is not None
    assert config.models.default_model == "sonnet-5"
    assert config.agents is not None
    assert set(config.agents.agents) == {"supervisor", "researcher", "writer"}
    assert config.guardrails is not None
    assert config.guardrails.redact_input is True
