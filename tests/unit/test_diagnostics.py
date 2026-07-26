"""Tests for the doctor diagnostic checks and the account/role probe matrix."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from haru.auth.cache import write_token_cache
from haru.auth.identity import SelectedIdentity, write_identity
from haru.auth.sso import ClientRegistration, SsoToken
from haru.config.schema import HaruConfig
from haru.diagnostics.checks import (
    check_bedrock_control_plane,
    check_caller_identity,
    check_guardrails,
    check_identity,
    check_inference_profiles,
    check_invoke,
    check_regions,
    check_token,
    run_checks,
)
from haru.diagnostics.matrix import probe_roles
from haru.errors import AuthError

START_URL = "https://example.awsapps.com/start"

MODEL = {"model_id": "anthropic.claude-sonnet-5", "region": "us-east-1", "max_tokens": 1024}

BASE: dict[str, Any] = {
    "app": {"name": "haru"},
    "auth": {
        "sso": {"start_url": START_URL, "sso_region": "us-east-1"},
        "bedrock_region": "us-east-1",
    },
    "models": {"default_model": "fast", "models": {"fast": MODEL}},
}


def make_config(**overrides: Any) -> HaruConfig:
    """Build a HaruConfig, merging top-level overrides."""
    payload: dict[str, Any] = {**BASE, **overrides}
    return HaruConfig.model_validate(payload)


def make_token(expires_in: timedelta = timedelta(hours=2)) -> SsoToken:
    """Build an SsoToken with the given remaining lifetime."""
    now = datetime.now(UTC).replace(microsecond=0)
    return SsoToken(
        access_token="access-abc",
        refresh_token="refresh-abc",
        expires_at=now + expires_in,
        registration=ClientRegistration(
            client_id="client-1", client_secret="secret-1", expires_at=now + timedelta(days=30)
        ),
    )


def aws_error(code: str) -> Exception:
    """Build an exception shaped like a botocore ClientError."""
    exc = Exception(code)
    exc.response = {"Error": {"Code": code, "Message": "nope"}}  # type: ignore[attr-defined]
    return exc


class FakeClient:
    """A boto3 client stand-in whose operations return or raise canned data."""

    def __init__(self, **operations: Any) -> None:
        self._operations = operations

    def __getattr__(self, name: str) -> Any:
        if name not in self._operations:
            raise AttributeError(name)
        outcome = self._operations[name]

        def _call(**_: Any) -> Any:
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return _call


class FakeSession:
    """A boto3 Session stand-in dispatching by service name."""

    def __init__(self, **clients: FakeClient) -> None:
        self._clients = clients

    def client(self, service: str, **_: Any) -> FakeClient:
        if service not in self._clients:
            raise AttributeError(service)
        return self._clients[service]


class FakeSso:
    """Identity Center stand-in with paginators and role credentials."""

    def __init__(
        self,
        accounts: list[dict[str, Any]],
        roles: dict[str, list[str]],
        denied_roles: frozenset[str] = frozenset(),
    ) -> None:
        self._accounts = accounts
        self._roles = roles
        self._denied = denied_roles

    def get_paginator(self, operation: str) -> Any:
        fake = self

        class _Paginator:
            def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
                if operation == "list_accounts":
                    return [{"accountList": fake._accounts}]
                roles = fake._roles.get(kwargs["accountId"], [])
                return [{"roleList": [{"roleName": name} for name in roles]}]

        return _Paginator()

    def get_role_credentials(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs["roleName"] in self._denied:
            raise ClientError(
                {"Error": {"Code": "ForbiddenException", "Message": "no"}}, "GetRoleCredentials"
            )
        return {
            "roleCredentials": {
                "accessKeyId": "AKIAEXAMPLE",
                "secretAccessKey": "secret",
                "sessionToken": "token",
            }
        }


def test_guardrails_checks() -> None:
    """Guardrails must carry an id when enabled."""
    assert check_guardrails(make_config()).status == "pass"
    enabled = make_config(guardrails={"enabled": True, "guardrail_id": "gr-1"})
    assert check_guardrails(enabled).status == "pass"
    broken = make_config(guardrails={"enabled": True})
    result = check_guardrails(broken)
    assert result.status == "fail"
    assert "guardrail_id" in result.remediation


def test_region_divergence_warns() -> None:
    """A model region differing from auth.bedrock_region is flagged."""
    assert [r.status for r in check_regions(make_config())] == ["pass"]
    divergent = make_config(
        models={"default_model": "fast", "models": {"fast": {**MODEL, "region": "eu-west-1"}}}
    )
    results = list(check_regions(divergent))
    assert results[0].status == "warn"
    assert "eu-west-1" in results[0].detail


def test_token_states(tmp_path: Path) -> None:
    """Token check reports missing, expiring, and healthy tokens."""
    config = make_config()
    assert check_token(config, tmp_path).status == "fail"

    write_token_cache(make_token(timedelta(minutes=5)), START_URL, "us-east-1", cache_dir=tmp_path)
    assert check_token(config, tmp_path).status == "warn"

    write_token_cache(make_token(), START_URL, "us-east-1", cache_dir=tmp_path)
    healthy = check_token(config, tmp_path)
    assert healthy.status == "pass"
    assert "access-abc" not in healthy.detail


def test_identity_states(tmp_path: Path) -> None:
    """Identity check reports absence, selection, and pinned values."""
    config = make_config()
    assert check_identity(config, tmp_path).status == "fail"

    write_identity(SelectedIdentity("111122223333", "Chosen"), START_URL, tmp_path)
    selected = check_identity(config, tmp_path)
    assert selected.status == "pass"
    assert "Chosen" in selected.detail

    pinned = make_config(
        auth={
            "sso": {"start_url": START_URL, "sso_region": "us-east-1", "role_name": "Pinned"},
            "bedrock_region": "us-east-1",
        }
    )
    result = check_identity(pinned, tmp_path)
    assert result.status == "warn"
    assert "pinned in config" in result.detail


def test_identity_warns_about_a_disproved_pin(tmp_path: Path) -> None:
    """A pin sign-in disproved is reported, not silently ignored."""
    write_identity(
        SelectedIdentity("111122223333", "Assigned-A", rejected_role_pin="HaruBedrockInvoke"),
        START_URL,
        tmp_path,
    )
    pinned = make_config(
        auth={
            "sso": {
                "start_url": START_URL,
                "sso_region": "us-east-1",
                "role_name": "HaruBedrockInvoke",
            },
            "bedrock_region": "us-east-1",
        }
    )

    result = check_identity(pinned, tmp_path)

    assert result.status == "warn"
    assert "Assigned-A" in result.detail
    assert "auth.sso.role_name: HaruBedrockInvoke" in (result.remediation or "")


def test_caller_identity_reports_arn() -> None:
    """The assumed-role ARN is surfaced for the AWS admin."""
    session = FakeSession(
        sts=FakeClient(
            get_caller_identity={"Arn": "arn:aws:sts::1:assumed-role/X/y", "Account": "1"}
        )
    )
    result = check_caller_identity(session)
    assert result.status == "pass"
    assert "assumed-role/X/y" in result.detail


def test_control_plane_denial_warns_not_fails() -> None:
    """A denied ListFoundationModels warns, since it does not prove invoke is denied."""
    session = FakeSession(
        bedrock=FakeClient(list_foundation_models=aws_error("AccessDeniedException"))
    )
    result = check_bedrock_control_plane(session, "us-east-1")
    assert result.status == "warn"
    assert "--invoke" in result.remediation


def test_inference_profile_check() -> None:
    """Configured models are probed for profile availability."""
    ok = FakeSession(bedrock=FakeClient(get_inference_profile={"inferenceProfileId": "x"}))
    assert [r.status for r in check_inference_profiles(ok, make_config())] == ["pass"]

    missing = FakeSession(
        bedrock=FakeClient(get_inference_profile=aws_error("ResourceNotFoundException"))
    )
    results = list(check_inference_profiles(missing, make_config()))
    assert results[0].status == "warn"
    assert "us.anthropic.claude-sonnet-5" in results[0].detail


def test_invoke_check_translates_denial(tmp_path: Path) -> None:
    """A denied live invoke names the required IAM actions."""
    session = FakeSession(
        **{"bedrock-runtime": FakeClient(converse=aws_error("AccessDeniedException"))}
    )
    result = check_invoke(make_config(), session)
    assert result.status == "fail"
    assert "bedrock:InvokeModelWithResponseStream" in result.iam_actions
    assert "bedrock:InvokeModelWithResponseStream" in result.remediation

    ok = FakeSession(**{"bedrock-runtime": FakeClient(converse={"output": {}})})
    assert check_invoke(make_config(), ok).status == "pass"


def test_run_checks_skips_after_signin_failure(tmp_path: Path) -> None:
    """Downstream checks are skipped, not raised, when sign-in fails."""
    results = list(run_checks(make_config(), tmp_path / "haru.yaml", cache_dir=tmp_path))
    statuses = {result.name: result.status for result in results}
    assert statuses["SSO token"] == "fail"
    assert statuses["Bedrock access"] == "skip"


def test_run_checks_full_pass(tmp_path: Path) -> None:
    """A healthy setup passes every stage and reports the live-invoke skip."""
    write_token_cache(make_token(), START_URL, "us-east-1", cache_dir=tmp_path)
    write_identity(SelectedIdentity("111122223333", "BedrockRole"), START_URL, tmp_path)
    session = FakeSession(
        sts=FakeClient(get_caller_identity={"Arn": "arn:aws:sts::1:assumed-role/X/y"}),
        bedrock=FakeClient(
            list_foundation_models={"modelSummaries": [{}, {}]},
            get_inference_profile={"inferenceProfileId": "x"},
        ),
    )

    results = list(
        run_checks(
            make_config(),
            tmp_path / "haru.yaml",
            cache_dir=tmp_path,
            session_factory=lambda *_: session,
        )
    )

    statuses = {result.name: result.status for result in results}
    assert statuses["AWS credentials"] == "pass"
    assert statuses["Caller identity"] == "pass"
    assert statuses["Bedrock control plane"] == "pass"
    assert statuses["Bedrock invoke (live)"] == "skip"
    assert not any(result.status == "fail" for result in results)


def seed_probe_env(tmp_path: Path) -> None:
    """Write a token so probe_roles can run."""
    write_token_cache(make_token(), START_URL, "us-east-1", cache_dir=tmp_path)


def test_probe_roles_matrix(tmp_path: Path, mocker: Any) -> None:
    """Every account and role is probed, and failures do not abort the sweep."""
    seed_probe_env(tmp_path)
    sso = FakeSso(
        [
            {"accountId": "111122223333", "accountName": "sandbox"},
            {"accountId": "444455556666", "accountName": "prod"},
        ],
        {"111122223333": ["Denied", "Works"], "444455556666": ["Blocked"]},
        denied_roles=frozenset({"Denied"}),
    )

    def fake_session_for_role(config: Any, token: Any, account: str, role: str, **_: Any) -> Any:
        if role == "Denied":
            raise AuthError(f"AWS rejected role {role!r}")
        if role == "Works":
            return FakeSession(bedrock=FakeClient(list_foundation_models={"modelSummaries": []}))
        return FakeSession(
            bedrock=FakeClient(list_foundation_models=aws_error("AccessDeniedException"))
        )

    mocker.patch("haru.diagnostics.matrix.session_for_role", side_effect=fake_session_for_role)

    probes = list(probe_roles(make_config(), cache_dir=tmp_path, sso_client=sso))

    assert [(p.role_name, p.credentials, p.bedrock) for p in probes] == [
        ("Denied", "denied", "skip"),
        ("Works", "ok", "ok"),
        ("Blocked", "ok", "denied"),
    ]
    assert [p.usable for p in probes] == [False, True, False]


def test_probe_roles_respects_filters(tmp_path: Path, mocker: Any) -> None:
    """--account narrows the sweep and --max-roles bounds it."""
    seed_probe_env(tmp_path)
    sso = FakeSso(
        [{"accountId": "111122223333"}, {"accountId": "444455556666"}],
        {"111122223333": ["A", "B", "C"], "444455556666": ["D"]},
    )
    mocker.patch(
        "haru.diagnostics.matrix.session_for_role",
        return_value=FakeSession(bedrock=FakeClient(list_foundation_models={"modelSummaries": []})),
    )

    filtered = list(
        probe_roles(
            make_config(), cache_dir=tmp_path, sso_client=sso, account_filter="444455556666"
        )
    )
    assert [p.role_name for p in filtered] == ["D"]

    bounded = list(probe_roles(make_config(), cache_dir=tmp_path, sso_client=sso, max_roles=2))
    assert len(bounded) == 2
