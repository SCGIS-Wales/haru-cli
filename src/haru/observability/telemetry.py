"""Configure OpenTelemetry tracing via the Strands telemetry helper.

Telemetry is a strict no-op when disabled; when enabled, the OTLP exporter is
initialised through ``StrandsTelemetry``, with the endpoint and service name
published via the standard OTel environment variables.
"""

import logging
import os

from haru.config.schema import ObservabilityConfig

logger = logging.getLogger(__name__)

_OTEL_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_OTEL_SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"


def configure_telemetry(obs_cfg: ObservabilityConfig | None) -> None:
    """Initialise OTel exporters when enabled; do nothing when disabled."""
    if obs_cfg is None or not obs_cfg.otel.enabled:
        return
    from strands.telemetry import StrandsTelemetry

    otel = obs_cfg.otel
    if otel.endpoint is not None:
        os.environ.setdefault(_OTEL_ENDPOINT_ENV, otel.endpoint)
    os.environ.setdefault(_OTEL_SERVICE_NAME_ENV, otel.service_name)

    telemetry = StrandsTelemetry()
    telemetry.setup_otlp_exporter()
    if otel.console_export:
        telemetry.setup_console_exporter()
    logger.debug("OpenTelemetry configured (service=%s)", otel.service_name)
