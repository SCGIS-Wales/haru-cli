"""Typed, immutable configuration schema for haru-cli.

Every YAML document under ``config/`` validates into one of these models at
load time. Models are frozen and reject unknown keys so configuration errors
surface immediately with the offending path.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _FrozenModel(BaseModel):
    """Base for all configuration models: immutable, strict about keys."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())


class AppConfig(_FrozenModel):
    """Top-level application settings."""

    name: str
    default_command: str = "chat"


class SsoConfig(_FrozenModel):
    """IAM Identity Center (SSO) login settings."""

    start_url: str
    sso_region: str
    registration_scopes: tuple[str, ...] = ("sso:account:access",)
    client_name: str = "haru-cli"
    account_id_env: str
    role_name: str
    callback_port: int = Field(default=0, ge=0, le=65535)
    browser: bool = True


class AuthConfig(_FrozenModel):
    """Authentication and AWS session settings."""

    sso: SsoConfig
    bedrock_region: str


class IncludesConfig(_FrozenModel):
    """Relative paths of the include files that complete the configuration."""

    models: str | None = None
    agents: str | None = None
    mcp: str | None = None
    guardrails: str | None = None
    logging: str | None = None


class SamplingConfig(_FrozenModel):
    """Optional sampling parameters.

    Unset fields are omitted from requests entirely, keeping the provider's
    defaults — required by Claude 5-series models, which reject non-default
    sampling values. ``seed`` is passed through for Bedrock models that
    support it (no Claude model does today).
    """

    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1)
    seed: int | None = None


class ModelConfig(_FrozenModel):
    """A single Bedrock model entry."""

    model_id: str
    region: str
    max_tokens: int = Field(ge=1)
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1)
    seed: int | None = None
    streaming: bool = True


class ModelsConfig(_FrozenModel):
    """The model catalogue and the default model key."""

    default_model: str
    models: dict[str, ModelConfig]

    @model_validator(mode="after")
    def _default_model_exists(self) -> Self:
        if self.default_model not in self.models:
            raise ValueError(f"default_model {self.default_model!r} is not a configured model")
        return self


class AgentConfig(_FrozenModel):
    """A single agent definition."""

    model: str
    system_prompt_ref: str | None = None
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    sampling: SamplingConfig | None = None


class SwarmConfig(_FrozenModel):
    """Swarm orchestration settings."""

    members: tuple[str, ...]
    max_handoffs: int = Field(default=8, ge=1)
    execution_timeout_seconds: int = Field(default=300, ge=1)


class GraphNode(_FrozenModel):
    """A node in a graph orchestration."""

    id: str
    agent: str


class GraphEdge(_FrozenModel):
    """A directed edge in a graph orchestration."""

    source: str = Field(alias="from")
    target: str = Field(alias="to")


class GraphConfig(_FrozenModel):
    """Graph orchestration settings."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    @model_validator(mode="after")
    def _edges_reference_nodes(self) -> Self:
        node_ids = {node.id for node in self.nodes}
        for edge in self.edges:
            for endpoint in (edge.source, edge.target):
                if endpoint not in node_ids:
                    raise ValueError(f"graph edge references unknown node {endpoint!r}")
        return self


class OrchestrationConfig(_FrozenModel):
    """Multi-agent orchestration settings."""

    default_pattern: Literal["supervisor", "swarm", "graph"] = "supervisor"
    swarm: SwarmConfig | None = None
    graph: GraphConfig | None = None


class AgentsConfig(_FrozenModel):
    """The agent roster and orchestration configuration."""

    agents: dict[str, AgentConfig]
    orchestration: OrchestrationConfig | None = None

    @model_validator(mode="after")
    def _references_are_defined(self) -> Self:
        if self.orchestration is None:
            return self
        if self.orchestration.swarm is not None:
            for member in self.orchestration.swarm.members:
                if member not in self.agents:
                    raise ValueError(f"swarm member {member!r} is not a configured agent")
        if self.orchestration.graph is not None:
            for node in self.orchestration.graph.nodes:
                if node.agent not in self.agents:
                    raise ValueError(f"graph node {node.id!r} references unknown agent")
        return self


class MCPServerConfig(_FrozenModel):
    """A single MCP server connection."""

    transport: Literal["stdio", "streamable-http"]
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    disabled: bool = False
    continue_on_error: bool = False

    @model_validator(mode="after")
    def _transport_fields(self) -> Self:
        if self.transport == "stdio" and self.command is None:
            raise ValueError("stdio MCP servers require a 'command'")
        if self.transport == "streamable-http" and self.url is None:
            raise ValueError("streamable-http MCP servers require a 'url'")
        return self


class MCPConfig(_FrozenModel):
    """The MCP server registry."""

    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class SessionsConfig(_FrozenModel):
    """Conversation persistence settings.

    The file backend always uses an explicit project-local directory; the OS
    temp directory is never used.
    """

    backend: Literal["file", "s3"] = "file"
    storage_dir: str = "./.haru/sessions"
    bucket: str | None = None
    prefix: str = "haru/sessions"
    region: str | None = None

    @model_validator(mode="after")
    def _s3_requires_bucket(self) -> Self:
        if self.backend == "s3" and self.bucket is None:
            raise ValueError("s3 session backend requires a 'bucket'")
        return self


class GuardrailsConfig(_FrozenModel):
    """Bedrock Guardrails settings."""

    enabled: bool = True
    guardrail_id: str | None = None
    guardrail_version: str = "DRAFT"
    trace: Literal["enabled", "disabled", "enabled_full"] = "enabled"
    redact_input: bool = True
    redact_input_message: str = "[redacted]"
    redact_output: bool = False


class LoggingConfig(_FrozenModel):
    """Application logging settings."""

    level: str = "INFO"
    format: Literal["json", "text"] = "json"
    file: str | None = None


class OtelConfig(_FrozenModel):
    """OpenTelemetry exporter settings."""

    enabled: bool = False
    endpoint: str | None = None
    service_name: str = "haru-cli"
    console_export: bool = False


class ObservabilityConfig(_FrozenModel):
    """Observability settings."""

    otel: OtelConfig


class GuardrailsFile(_FrozenModel):
    """Shape of the guardrails include file."""

    guardrails: GuardrailsConfig


class LoggingFile(_FrozenModel):
    """Shape of the logging include file."""

    logging: LoggingConfig
    observability: ObservabilityConfig


class HaruConfig(_FrozenModel):
    """The complete haru-cli configuration after includes are resolved."""

    app: AppConfig
    auth: AuthConfig
    sessions: SessionsConfig | None = None
    includes: IncludesConfig | None = None
    models: ModelsConfig | None = None
    agents: AgentsConfig | None = None
    mcp: MCPConfig | None = None
    guardrails: GuardrailsConfig | None = None
    logging: LoggingConfig | None = None
    observability: ObservabilityConfig | None = None
