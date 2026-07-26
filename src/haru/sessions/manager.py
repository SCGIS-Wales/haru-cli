"""Build Strands session managers from typed configuration.

The file backend persists to an explicit project-local directory
(``./.haru/sessions`` by default) — never the OS temp directory. The S3
backend stores sessions under a configurable bucket and prefix.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from haru.config.schema import HaruConfig, SessionsConfig

if TYPE_CHECKING:
    import boto3
    from strands.session.session_manager import SessionManager

_SESSION_DIR_PREFIX = "session_"

_DEFAULT_SESSIONS = SessionsConfig()


def build_session_manager(
    config: HaruConfig, session_id: str, *, boto_session: boto3.Session | None = None
) -> SessionManager:
    """Build the configured session manager (file by default) for ``session_id``."""
    from strands.session import FileSessionManager, S3SessionManager

    sessions = config.sessions if config.sessions is not None else _DEFAULT_SESSIONS
    if sessions.backend == "s3":
        return S3SessionManager(
            session_id,
            bucket=str(sessions.bucket),
            prefix=sessions.prefix,
            boto_session=boto_session,
            region_name=sessions.region,
        )
    storage_dir = Path(sessions.storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    return FileSessionManager(session_id, storage_dir=str(storage_dir))


def list_sessions(
    config: HaruConfig | None = None, *, boto_session: boto3.Session | None = None
) -> list[str]:
    """Return the stored session ids for the configured backend, sorted."""
    sessions = (
        config.sessions if config is not None and config.sessions is not None else _DEFAULT_SESSIONS
    )
    if sessions.backend == "s3":
        return _list_s3_sessions(sessions, boto_session)
    storage_dir = Path(sessions.storage_dir)
    if not storage_dir.is_dir():
        return []
    return sorted(
        entry.name.removeprefix(_SESSION_DIR_PREFIX)
        for entry in storage_dir.iterdir()
        if entry.is_dir() and entry.name.startswith(_SESSION_DIR_PREFIX)
    )


def _list_s3_sessions(sessions: SessionsConfig, boto_session: boto3.Session | None) -> list[str]:
    import boto3

    session = boto_session if boto_session is not None else boto3.Session()
    client = session.client("s3", region_name=sessions.region)
    prefix = f"{sessions.prefix.rstrip('/')}/{_SESSION_DIR_PREFIX}" if sessions.prefix else ""
    found: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=str(sessions.bucket), Prefix=prefix):
        for item in page.get("Contents", []):
            remainder = item["Key"].removeprefix(prefix)
            session_id = remainder.split("/", 1)[0]
            if session_id:
                found.add(session_id)
    return sorted(found)
