"""The ``haru doctor`` command: diagnose authentication and Bedrock access."""

from __future__ import annotations

import json as json_module
from pathlib import Path
from typing import Any

import click

from haru.commands._common import load_cli_config
from haru.errors import HaruError

_MARK = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "skip": "SKIP"}


@click.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to haru.yaml (default: ./config/haru.yaml, then ~/.config/haru/haru.yaml).",
)
@click.option(
    "--invoke",
    is_flag=True,
    help="Make a real minimal Bedrock call (billable) for a definitive answer.",
)
@click.option(
    "--all-roles",
    "all_roles",
    is_flag=True,
    help="Probe every account and role you are assigned for Bedrock access.",
)
@click.option("--account", "account_filter", default=None, help="Limit --all-roles to one account.")
@click.option(
    "--max-roles", default=25, show_default=True, help="Cap combinations probed by --all-roles."
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def doctor(  # noqa: PLR0913, PLR0917 - one option per flag; Click passes positionally
    config_path: Path | None,
    invoke: bool,
    all_roles: bool,
    account_filter: str | None,
    max_roles: int,
    as_json: bool,
) -> None:
    """Check configuration, sign-in, and Bedrock permissions."""
    from haru.diagnostics.checks import run_checks
    from haru.diagnostics.matrix import probe_roles

    try:
        resolved, config = load_cli_config(config_path)
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc

    if all_roles:
        try:
            probes = list(
                probe_roles(
                    config,
                    invoke=invoke,
                    account_filter=account_filter,
                    max_roles=max_roles,
                )
            )
        except HaruError as exc:
            raise click.ClickException(str(exc)) from exc
        _report_matrix(probes, invoke=invoke, as_json=as_json)
        return

    try:
        results = list(run_checks(config, resolved, invoke=invoke))
    except HaruError as exc:
        raise click.ClickException(str(exc)) from exc
    _report_checks(results, as_json=as_json)
    if any(result.status == "fail" for result in results):
        raise click.exceptions.Exit(1)


def _report_checks(results: list[Any], *, as_json: bool) -> None:
    """Print check results as text or JSON."""
    if as_json:
        click.echo(
            json_module.dumps(
                [
                    {
                        "name": result.name,
                        "status": result.status,
                        "detail": result.detail,
                        "remediation": result.remediation,
                        "iam_actions": list(result.iam_actions),
                    }
                    for result in results
                ],
                indent=2,
            )
        )
        return
    for result in results:
        click.echo(f"{_MARK[result.status]}  {result.name}")
        click.echo(f"      {result.detail}")
        if result.remediation:
            for line in result.remediation.splitlines():
                click.echo(f"      {line}")
        click.echo()
    failures = sum(1 for result in results if result.status == "fail")
    warnings = sum(1 for result in results if result.status == "warn")
    click.echo(f"{failures} failed, {warnings} warnings, {len(results)} checks.")
    if failures:
        click.echo("Run 'haru doctor --all-roles' to find an account and role that works.")


def _report_matrix(probes: list[Any], *, invoke: bool, as_json: bool) -> None:
    """Print the account/role probe matrix and a verdict.

    ``invoke`` says whether a real Bedrock call was made. Without it only the
    control plane was tested, and the verdict must not claim more than that.
    """
    if as_json:
        click.echo(
            json_module.dumps(
                [
                    {
                        "account_id": probe.account_id,
                        "account_name": probe.account_name,
                        "role_name": probe.role_name,
                        "credentials": probe.credentials,
                        "bedrock": probe.bedrock,
                        "invoke": probe.invoke,
                        "detail": probe.detail,
                        "usable": probe.usable,
                        "proven": probe.proven,
                    }
                    for probe in probes
                ],
                indent=2,
            )
        )
        return
    if not probes:
        click.echo("No accounts or roles are assigned to this sign-in.")
        return
    click.echo(f"{'ACCOUNT':<14}{'NAME':<22}{'ROLE':<28}{'CREDS':<8}{'BEDROCK':<10}INVOKE")
    for probe in probes:
        click.echo(
            f"{probe.account_id:<14}{(probe.account_name or '-'):<22}"
            f"{probe.role_name:<28}{probe.credentials:<8}{probe.bedrock:<10}{probe.invoke}"
        )
    click.echo()
    usable = [probe for probe in probes if probe.usable]
    if usable:
        best = usable[0]
        click.echo(
            f"Use account {best.account_id}, role {best.role_name}:"
            " re-run 'haru login' and choose it, or pin it with auth.sso.account_id"
            " and auth.sso.role_name."
        )
        if not best.proven:
            click.echo(
                "That is based on ListFoundationModels alone, which is indicative"
                " rather than proof. Re-run with --invoke to confirm the role can"
                " actually invoke a model."
            )
        return
    if invoke:
        click.echo(
            "No assigned role could reach Bedrock. None of your permission sets grant"
            " bedrock:InvokeModel / bedrock:InvokeModelWithResponseStream."
            " Ask your AWS admin for a Bedrock permission set - see docs/troubleshooting.md."
        )
        return
    click.echo(
        "No assigned role could list Bedrock models (bedrock:ListFoundationModels).\n"
        "That is the control plane only. A role granted bedrock:InvokeModel on a\n"
        "specific model ARN without ListFoundationModels - a common least-privilege\n"
        "setup - looks identical here.\n"
        "Re-run 'haru doctor --all-roles --invoke' for a definitive answer"
        " (one minimal billable Bedrock call per role)."
    )
