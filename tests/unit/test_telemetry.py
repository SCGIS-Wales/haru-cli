"""Tests for OpenTelemetry configuration."""

import os
from typing import Any

import pytest

from haru.config.schema import ObservabilityConfig
from haru.observability.telemetry import configure_telemetry


def make_obs(**otel: Any) -> ObservabilityConfig:
    """Build an ObservabilityConfig with the given otel settings."""
    return ObservabilityConfig.model_validate({"otel": otel})


def test_disabled_is_noop(mocker: Any) -> None:
    """Disabled telemetry never touches the OTel SDK."""
    telemetry_cls = mocker.patch("haru.observability.telemetry.StrandsTelemetry")
    configure_telemetry(make_obs(enabled=False))
    telemetry_cls.assert_not_called()


def test_missing_section_is_noop(mocker: Any) -> None:
    """No observability section means no telemetry setup."""
    telemetry_cls = mocker.patch("haru.observability.telemetry.StrandsTelemetry")
    configure_telemetry(None)
    telemetry_cls.assert_not_called()


def test_enabled_initialises_otlp(mocker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled telemetry sets env vars and initialises the OTLP exporter."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    telemetry_cls = mocker.patch("haru.observability.telemetry.StrandsTelemetry")

    configure_telemetry(
        make_obs(enabled=True, endpoint="https://otel.example.com:4317", service_name="haru-cli")
    )

    telemetry_cls.assert_called_once_with()
    telemetry_cls.return_value.setup_otlp_exporter.assert_called_once_with()
    telemetry_cls.return_value.setup_console_exporter.assert_not_called()
    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://otel.example.com:4317"
    assert os.environ["OTEL_SERVICE_NAME"] == "haru-cli"


def test_console_export_opt_in(mocker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """console_export additionally wires the console exporter."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    telemetry_cls = mocker.patch("haru.observability.telemetry.StrandsTelemetry")

    configure_telemetry(make_obs(enabled=True, console_export=True))

    telemetry_cls.return_value.setup_console_exporter.assert_called_once_with()


def test_existing_env_not_overwritten(mocker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly exported OTel env vars win over config values."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://operator.example.com:4317")
    mocker.patch("haru.observability.telemetry.StrandsTelemetry")

    configure_telemetry(make_obs(enabled=True, endpoint="https://config.example.com:4317"))

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://operator.example.com:4317"
