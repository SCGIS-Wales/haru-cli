"""Tests for RFC 7636 PKCE pair generation."""

import base64
import hashlib
import re

from haru.auth.pkce import code_challenge_from, generate_pkce_pair

URLSAFE_UNPADDED = re.compile(r"^[A-Za-z0-9_-]+$")

# RFC 7636 appendix B known-answer vector.
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_verifier_shape() -> None:
    """The verifier is 43 chars of unpadded URL-safe base64 (32 random bytes)."""
    verifier, _ = generate_pkce_pair()
    assert len(verifier) == 43
    assert URLSAFE_UNPADDED.match(verifier)
    assert "=" not in verifier


def test_challenge_is_s256_of_verifier() -> None:
    """The challenge is the unpadded URL-safe base64 SHA-256 of the verifier."""
    verifier, challenge = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected
    assert URLSAFE_UNPADDED.match(challenge)


def test_rfc7636_known_vector() -> None:
    """The S256 derivation matches the RFC 7636 appendix B example."""
    assert code_challenge_from(RFC_VERIFIER) == RFC_CHALLENGE


def test_pairs_are_unique() -> None:
    """Each call produces a fresh verifier."""
    first, _ = generate_pkce_pair()
    second, _ = generate_pkce_pair()
    assert first != second
