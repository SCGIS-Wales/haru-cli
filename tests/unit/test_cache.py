"""Tests for the botocore-compatible SSO token cache."""

import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from haru.auth.cache import cache_path, read_token_cache, write_token_cache
from haru.auth.sso import ClientRegistration, SsoToken
from haru.errors import AuthError

START_URL = "https://example.awsapps.com/start"
REGION = "us-east-1"


def make_token(refresh_token: str | None = "refresh-abc") -> SsoToken:
    """Build a token with second-precision timestamps (the cache format)."""
    now = datetime.now(UTC).replace(microsecond=0)
    registration = ClientRegistration(
        client_id="client-123",
        client_secret="client-secret",
        expires_at=now + timedelta(days=90),
    )
    return SsoToken(
        access_token="access-abc",
        refresh_token=refresh_token,
        expires_at=now + timedelta(hours=1),
        registration=registration,
    )


def test_cache_filename_is_sha1_of_start_url(tmp_path: Path) -> None:
    """The cache file name is the SHA-1 hex digest of the start URL."""
    expected = hashlib.sha1(START_URL.encode("utf-8"), usedforsecurity=False).hexdigest()
    path = cache_path(START_URL, tmp_path)
    assert path == tmp_path / f"{expected}.json"


def test_write_token_cache_schema(tmp_path: Path) -> None:
    """The cache JSON matches the botocore SSOTokenProvider schema."""
    token = make_token()
    path = write_token_cache(token, START_URL, REGION, cache_dir=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["startUrl"] == START_URL
    assert payload["region"] == REGION
    assert payload["accessToken"] == "access-abc"
    assert payload["refreshToken"] == "refresh-abc"
    assert payload["clientId"] == "client-123"
    assert payload["clientSecret"] == "client-secret"
    assert payload["expiresAt"].endswith("Z")
    assert payload["registrationExpiresAt"].endswith("Z")
    datetime.strptime(payload["expiresAt"], "%Y-%m-%dT%H:%M:%SZ")


def test_write_token_cache_permissions(tmp_path: Path) -> None:
    """The cache file is written with 0600 permissions."""
    path = write_token_cache(make_token(), START_URL, REGION, cache_dir=tmp_path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_write_omits_absent_refresh_token(tmp_path: Path) -> None:
    """No refreshToken key is written when the token has none."""
    path = write_token_cache(make_token(refresh_token=None), START_URL, REGION, cache_dir=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "refreshToken" not in payload


def test_roundtrip(tmp_path: Path) -> None:
    """A written token reads back identically."""
    token = make_token()
    write_token_cache(token, START_URL, REGION, cache_dir=tmp_path)
    loaded = read_token_cache(START_URL, cache_dir=tmp_path)
    assert loaded == token


def test_read_missing_cache_returns_none(tmp_path: Path) -> None:
    """A missing cache file reads as None."""
    assert read_token_cache(START_URL, cache_dir=tmp_path) is None


def test_read_corrupt_cache_raises(tmp_path: Path) -> None:
    """A corrupt cache file raises AuthError pointing at the path."""
    path = cache_path(START_URL, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(AuthError, match="Corrupt"):
        read_token_cache(START_URL, cache_dir=tmp_path)


def test_overwrite_replaces_existing(tmp_path: Path) -> None:
    """Writing twice replaces the cache content in place."""
    first = make_token()
    write_token_cache(first, START_URL, REGION, cache_dir=tmp_path)
    second = SsoToken(
        access_token="access-new",
        refresh_token=first.refresh_token,
        expires_at=first.expires_at,
        registration=first.registration,
    )
    write_token_cache(second, START_URL, REGION, cache_dir=tmp_path)
    loaded = read_token_cache(START_URL, cache_dir=tmp_path)
    assert loaded is not None
    assert loaded.access_token == "access-new"
