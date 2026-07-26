"""Read-only diagnostics for authentication, permissions, and configuration."""

from haru.diagnostics.checks import CheckResult, Status, run_checks
from haru.diagnostics.matrix import RoleProbe, probe_roles

__all__ = ["CheckResult", "RoleProbe", "Status", "probe_roles", "run_checks"]
