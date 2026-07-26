"""Tests for the ``haru doctor`` command layer."""

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from haru.cli import cli
from haru.diagnostics.checks import CheckResult
from haru.diagnostics.matrix import RoleProbe

CONFIG = """\
app:
  name: haru
auth:
  sso:
    start_url: https://example.awsapps.com/start
    sso_region: us-east-1
  bedrock_region: us-east-1
"""


def write_config(tmp_path: Path) -> Path:
    """Write a minimal config and return its path."""
    path = tmp_path / "haru.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def test_reports_checks_and_exits_zero(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """Passing checks print with a summary and exit 0."""
    mocker.patch(
        "haru.diagnostics.checks.run_checks",
        return_value=iter([CheckResult("SSO token", "pass", "Valid for 90 min.")]),
    )

    result = runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "PASS  SSO token" in result.output
    assert "0 failed" in result.output


def test_failure_exits_one_and_points_at_all_roles(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """A failing check exits 1 and suggests the role sweep."""
    mocker.patch(
        "haru.diagnostics.checks.run_checks",
        return_value=iter(
            [
                CheckResult(
                    "Bedrock invoke (live)",
                    "fail",
                    "Converse failed.",
                    remediation="Grant bedrock:InvokeModelWithResponseStream.",
                    iam_actions=("bedrock:InvokeModelWithResponseStream",),
                )
            ]
        ),
    )

    result = runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path))])

    assert result.exit_code == 1
    assert "FAIL  Bedrock invoke (live)" in result.output
    assert "bedrock:InvokeModelWithResponseStream" in result.output
    assert "--all-roles" in result.output


def test_warnings_do_not_fail(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """Warnings are reported but keep the exit code at 0."""
    mocker.patch(
        "haru.diagnostics.checks.run_checks",
        return_value=iter([CheckResult("Regions", "warn", "Model region differs.")]),
    )

    result = runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path))])

    assert result.exit_code == 0
    assert "WARN  Regions" in result.output
    assert "1 warnings" in result.output


def test_json_output_is_parseable(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """--json emits machine-readable results for support tickets."""
    mocker.patch(
        "haru.diagnostics.checks.run_checks",
        return_value=iter([CheckResult("SSO token", "pass", "Valid.", iam_actions=("a:b",))]),
    )

    result = runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path)), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["name"] == "SSO token"
    assert payload[0]["iam_actions"] == ["a:b"]


def test_invoke_flag_is_forwarded(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """--invoke reaches run_checks."""
    run_checks = mocker.patch("haru.diagnostics.checks.run_checks", return_value=iter([]))

    runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path)), "--invoke"])

    assert run_checks.call_args.kwargs["invoke"] is True


def test_all_roles_matrix_and_verdict(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """--all-roles prints the matrix and names a working combination."""
    mocker.patch(
        "haru.diagnostics.matrix.probe_roles",
        return_value=iter(
            [
                RoleProbe("881490127383", "amazonq-prod", "TRPAmazonQUsers", "ok", "denied"),
                RoleProbe("111122223333", "ml-sandbox", "BedrockDeveloper", "ok", "ok"),
            ]
        ),
    )

    result = runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path)), "--all-roles"])

    assert result.exit_code == 0, result.output
    assert "TRPAmazonQUsers" in result.output
    assert "BedrockDeveloper" in result.output
    assert "Use account 111122223333, role BedrockDeveloper" in result.output


def test_all_roles_verdict_is_definitive_with_invoke(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """A real Converse call was made, so the strong claim is earned."""
    mocker.patch(
        "haru.diagnostics.matrix.probe_roles",
        return_value=iter(
            [RoleProbe("881490127383", "prod", "TRPAmazonQUsers", "ok", "denied", "denied")]
        ),
    )

    result = runner.invoke(
        cli, ["doctor", "--config", str(write_config(tmp_path)), "--all-roles", "--invoke"]
    )

    assert "No assigned role could reach Bedrock" in result.output
    assert "bedrock:InvokeModel" in result.output


def test_all_roles_verdict_is_hedged_without_invoke(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """Only the control plane was tested, so the verdict must not claim more."""
    mocker.patch(
        "haru.diagnostics.matrix.probe_roles",
        return_value=iter([RoleProbe("881490127383", "prod", "TRPAmazonQUsers", "ok", "denied")]),
    )

    result = runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path)), "--all-roles"])

    assert "control plane only" in result.output
    assert "--all-roles --invoke" in result.output
    assert "None of your permission sets grant" not in result.output


def test_all_roles_usable_by_control_plane_only_is_hedged(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """A recommendation from ListFoundationModels alone is flagged as unproven."""
    mocker.patch(
        "haru.diagnostics.matrix.probe_roles",
        return_value=iter([RoleProbe("111122223333", "sandbox", "BedrockDeveloper", "ok", "ok")]),
    )

    result = runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path)), "--all-roles"])

    assert "Use account 111122223333, role BedrockDeveloper" in result.output
    assert "indicative rather than proof" in result.output


def test_all_roles_json_reports_proven(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """A skipped invoke is never reported as proven, even when usable."""
    mocker.patch(
        "haru.diagnostics.matrix.probe_roles",
        return_value=iter([RoleProbe("1", "n", "R", "ok", "ok")]),
    )

    result = runner.invoke(
        cli, ["doctor", "--config", str(write_config(tmp_path)), "--all-roles", "--json"]
    )

    payload = json.loads(result.output)
    assert payload[0]["usable"] is True
    assert payload[0]["proven"] is False


def test_all_roles_json(runner: CliRunner, tmp_path: Path, mocker: Any) -> None:
    """--all-roles --json emits parseable probe rows."""
    mocker.patch(
        "haru.diagnostics.matrix.probe_roles",
        return_value=iter([RoleProbe("1", "n", "R", "ok", "ok")]),
    )

    result = runner.invoke(
        cli, ["doctor", "--config", str(write_config(tmp_path)), "--all-roles", "--json"]
    )

    payload = json.loads(result.output)
    assert payload[0]["role_name"] == "R"
    assert payload[0]["usable"] is True


CONFIG_WITH_MODEL = (
    CONFIG
    + """\
models:
  default_model: sonnet
  models:
    sonnet:
      model_id: anthropic.claude-sonnet-5
      region: us-east-1
      max_tokens: 4096
"""
)


def test_admin_request_is_pasteable_and_evidence_backed(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """--all-roles --admin-request emits a complete request with the probe matrix."""
    mocker.patch(
        "haru.diagnostics.matrix.probe_roles",
        return_value=iter(
            [RoleProbe("881490127383", "amazonq-prod", "TRPAmazonQUsers", "ok", "denied")]
        ),
    )
    path = tmp_path / "haru.yaml"
    path.write_text(CONFIG_WITH_MODEL, encoding="utf-8")

    result = runner.invoke(cli, ["doctor", "--config", str(path), "--all-roles", "--admin-request"])

    assert result.exit_code == 0, result.output
    out = result.output
    # Evidence from the probe.
    assert "TRPAmazonQUsers" in out
    assert "https://example.awsapps.com/start" in out
    # The policy, including the per-region foundation-model ARNs.
    assert "bedrock:InvokeModelWithResponseStream" in out
    assert "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-*" in out
    assert "881490127383:inference-profile/us.anthropic.claude-*" in out
    # The configured model and the Kiro explanation.
    assert "us.anthropic.claude-sonnet-5 in us-east-1" in out
    assert "different services" in out
    # It is valid JSON where the policy block is.
    start = out.index("{")
    end = out.index("}\n\nModels") + 1
    json.loads(out[start:end])


def test_admin_request_requires_all_roles(runner: CliRunner, tmp_path: Path) -> None:
    """--admin-request without --all-roles is rejected, not silently ignored."""
    result = runner.invoke(
        cli, ["doctor", "--config", str(write_config(tmp_path)), "--admin-request"]
    )

    assert result.exit_code != 0
    assert "needs --all-roles" in result.output


def test_denied_verdict_points_at_admin_request(
    runner: CliRunner, tmp_path: Path, mocker: Any
) -> None:
    """The plain denied verdict names the admin-request flag."""
    mocker.patch(
        "haru.diagnostics.matrix.probe_roles",
        return_value=iter([RoleProbe("881490127383", "prod", "TRPAmazonQUsers", "ok", "denied")]),
    )

    result = runner.invoke(cli, ["doctor", "--config", str(write_config(tmp_path)), "--all-roles"])

    assert "authorization gap, not an authentication one" in result.output
    assert "--admin-request" in result.output


def test_missing_config_is_clean(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No configuration exits cleanly with the init hint, not a traceback."""
    monkeypatch.delenv("HARU_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "haru config init" in result.output
    assert "Traceback" not in result.output
