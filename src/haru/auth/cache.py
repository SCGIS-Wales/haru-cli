"""Botocore-compatible SSO token cache under ``~/.aws/sso/cache``.

The cache file name is the SHA-1 of the start URL, and the JSON schema matches
what botocore's ``SSOTokenProvider`` reads, so boto3 tooling can consume and
refresh the same cache. Files are written with 0600 permissions.
"""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from haru.auth.sso import ClientRegistration, SsoToken
from haru.errors import AuthError

_DEFAULT_CACHE_DIR = Path("~") / ".aws" / "sso" / "cache"
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def cache_path(start_url: str, cache_dir: Path | None = None) -> Path:
    """Return the cache file path for ``start_url`` (SHA-1 hex name, .json)."""
    directory = cache_dir if cache_dir is not None else _DEFAULT_CACHE_DIR.expanduser()
    digest = hashlib.sha1(start_url.encode("utf-8"), usedforsecurity=False).hexdigest()
    return directory / f"{digest}.json"


def write_token_cache(
    token: SsoToken, start_url: str, region: str, cache_dir: Path | None = None
) -> Path:
    """Write ``token`` to the botocore-compatible cache and return the path."""
    path = cache_path(start_url, cache_dir)
    payload: dict[str, str] = {
        "startUrl": start_url,
        "region": region,
        "accessToken": token.access_token,
        "expiresAt": _format_timestamp(token.expires_at),
        "clientId": token.registration.client_id,
        "clientSecret": token.registration.client_secret,
        "registrationExpiresAt": _format_timestamp(token.registration.expires_at),
    }
    if token.refresh_token is not None:
        payload["refreshToken"] = token.refresh_token

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    path.chmod(0o600)
    return path


def read_token_cache(start_url: str, cache_dir: Path | None = None) -> SsoToken | None:
    """Read the cached token for ``start_url``; return None when absent."""
    path = cache_path(start_url, cache_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        registration = ClientRegistration(
            client_id=payload["clientId"],
            client_secret=payload["clientSecret"],
            expires_at=_parse_timestamp(payload["registrationExpiresAt"]),
        )
        return SsoToken(
            access_token=payload["accessToken"],
            refresh_token=payload.get("refreshToken"),
            expires_at=_parse_timestamp(payload["expiresAt"]),
            registration=registration,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise AuthError(f"Corrupt SSO token cache at {path}; run 'haru login'") from exc


def _format_timestamp(value: datetime) -> str:
    """Format a datetime as the UTC timestamp string botocore expects."""
    return value.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)


def _parse_timestamp(value: str) -> datetime:
    """Parse a botocore UTC timestamp string."""
    return datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
