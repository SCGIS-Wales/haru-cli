"""Guard against heavy imports creeping back into CLI startup."""

import subprocess
import sys

_HEAVY_MODULES = ("strands", "strands_tools", "mcp", "boto3", "botocore", "opentelemetry")

_CHECK = (
    "import sys; import haru.cli; "
    f"heavy = [m for m in {_HEAVY_MODULES!r} "
    "if m in sys.modules or any(k.startswith(m + '.') for k in sys.modules)]; "
    "assert not heavy, f'heavy modules imported at CLI startup: {heavy}'"
)


def test_cli_import_stays_light() -> None:
    """Importing haru.cli must not import the AWS/agent stack.

    Keeps `haru --help` / `--version` fast: strands, mcp, boto3 and friends
    load only when a command actually needs them.
    """
    result = subprocess.run(
        [sys.executable, "-c", _CHECK], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
