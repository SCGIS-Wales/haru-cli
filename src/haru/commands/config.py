"""The ``haru config`` command group: initialise and inspect configuration."""

from importlib import resources
from pathlib import Path

import click

from haru.auth.identity import effective_identity
from haru.commands._common import load_cli_config
from haru.config import user_config_path
from haru.config.schema import ModelConfig
from haru.errors import HaruError


def _sampling_summary(entry: ModelConfig) -> str:
    """Compact ``[temp=.. top_k=..]`` summary of a model's set sampling fields."""
    labels = {"temperature": "temp", "top_p": "top_p", "top_k": "top_k", "seed": "seed"}
    parts = [
        f"{label}={value}"
        for field, label in labels.items()
        if (value := getattr(entry, field)) is not None
    ]
    return f" [{' '.join(parts)}]" if parts else ""


_TEMPLATE_FILES = ("haru.yaml", "models.yaml", "agents.yaml", "mcp.yaml", "logging.yaml")
_PROMPT_FILES = ("supervisor.md", "researcher.md", "writer.md")

_GUARDRAILS_ENABLED = """\
guardrails:
  enabled: true
  guardrail_id: {guardrail_id}
  guardrail_version: DRAFT
  trace: enabled
  redact_input: true
  redact_input_message: "[redacted]"
  redact_output: false
"""

_GUARDRAILS_DISABLED = """\
guardrails:
  # Explicitly disabled at init time. Set enabled: true and a guardrail_id
  # before production use; guardrails fail closed when enabled without an id.
  enabled: false
"""


@click.group()
def config() -> None:
    """Manage haru configuration."""


@config.command()
@click.option(
    "--dir",
    "target_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Target directory (default: ~/.config/haru).",
)
@click.option(
    "--start-url",
    prompt="IAM Identity Center start URL",
    help="Your AWS access portal URL (https://...awsapps.com/start).",
)
@click.option(
    "--sso-region", default="us-east-1", prompt="Identity Center region", show_default=True
)
@click.option("--bedrock-region", default="us-east-1", prompt="Bedrock region", show_default=True)
@click.option(
    "--guardrail-id",
    default="",
    prompt="Bedrock guardrail id (blank to disable)",
    help="Guardrail id; leaving it blank disables guardrails explicitly.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing configuration.")
def init(  # noqa: PLR0913, PLR0917 - one option per prompted setting; Click passes positionally
    target_dir: Path | None,
    start_url: str,
    sso_region: str,
    bedrock_region: str,
    guardrail_id: str,
    force: bool,
) -> None:
    """Create a starter configuration tree (config, includes, prompts)."""
    target = target_dir if target_dir is not None else user_config_path().parent
    base_file = target / "haru.yaml"
    if base_file.exists() and not force:
        raise click.ClickException(f"{base_file} already exists; use --force to overwrite.")

    replacements = {
        "__START_URL__": start_url,
        "__SSO_REGION__": sso_region,
        "__BEDROCK_REGION__": bedrock_region,
    }
    templates = resources.files("haru.config.templates")
    (target / "prompts").mkdir(parents=True, exist_ok=True)
    for name in _TEMPLATE_FILES:
        text = templates.joinpath(name).read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        (target / name).write_text(text, encoding="utf-8")
    for name in _PROMPT_FILES:
        text = templates.joinpath("prompts").joinpath(name).read_text(encoding="utf-8")
        (target / "prompts" / name).write_text(text, encoding="utf-8")

    if guardrail_id:
        guardrails = _GUARDRAILS_ENABLED.format(guardrail_id=guardrail_id)
    else:
        guardrails = _GUARDRAILS_DISABLED
    (target / "guardrails.yaml").write_text(guardrails, encoding="utf-8")

    click.echo(f"Configuration written to {target}.")
    click.echo("Next steps:")
    click.echo("  1. haru login   (browser sign-in; your account and role are discovered)")
    click.echo("  2. haru chat")


@config.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: ./config/haru.yaml, then ~/.config/haru/haru.yaml).",
)
def show(config_path: Path | None) -> None:
    """Show the resolved configuration (no secrets)."""
    try:
        resolved, loaded = load_cli_config(config_path)
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Config file:  {resolved}")
    click.echo(f"App:          {loaded.app.name}")
    click.echo(f"SSO start:    {loaded.auth.sso.start_url}")
    click.echo(f"Bedrock:      {loaded.auth.bedrock_region}")
    account, account_source, role, role_source = effective_identity(loaded.auth.sso)
    if account is not None and role is not None:
        click.echo(
            f"Identity:     account {account} ({account_source}), role {role} ({role_source})"
        )
    else:
        click.echo("Identity:     not selected - run 'haru login'")
    if loaded.models is not None:
        default = loaded.models.default_model
        names = ", ".join(
            f"{name}{' (default)' if name == default else ''}{_sampling_summary(entry)}"
            for name, entry in sorted(loaded.models.models.items())
        )
        click.echo(f"Models:       {names}")
    if loaded.agents is not None:
        click.echo(f"Agents:       {', '.join(sorted(loaded.agents.agents))}")
        if loaded.agents.orchestration is not None:
            click.echo(f"Orchestration: {loaded.agents.orchestration.default_pattern}")
    if loaded.mcp is not None and loaded.mcp.mcp_servers:
        servers = ", ".join(
            f"{name}{' (disabled)' if server.disabled else ''}"
            for name, server in sorted(loaded.mcp.mcp_servers.items())
        )
        click.echo(f"MCP servers:  {servers}")
    guardrails = (
        "enabled" if loaded.guardrails is not None and loaded.guardrails.enabled else "disabled"
    )
    click.echo(f"Guardrails:   {guardrails}")
    sessions = loaded.sessions.backend if loaded.sessions is not None else "file"
    click.echo(f"Sessions:     {sessions}")
