"""RFC 7636 PKCE helpers for the IAM Identity Center login flow."""

import base64
import hashlib
import secrets


def generate_pkce_pair() -> tuple[str, str]:
    """Return a ``(code_verifier, code_challenge)`` pair using the S256 method.

    The verifier is 32 random bytes encoded as unpadded URL-safe base64; the
    challenge is the unpadded URL-safe base64 SHA-256 of the verifier.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    return verifier, code_challenge_from(verifier)


def code_challenge_from(verifier: str) -> str:
    """Derive the S256 code challenge for ``verifier`` (RFC 7636, section 4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
