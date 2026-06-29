import time

import jwt
import pytest

from backend import auth


def test_role_ranking():
    assert auth.role_at_least("admin", "viewer")
    assert auth.role_at_least("reviewer", "reviewer")
    assert not auth.role_at_least("viewer", "reviewer")
    assert not auth.role_at_least("nonsense", "viewer")
    assert not auth.role_at_least("admin", "nonsense_minimum")


def test_api_key_hash_roundtrip_and_wrong_key_fails():
    salt = auth.new_salt()
    raw = auth.generate_api_key("acme")
    assert raw.startswith("pk_acme_")
    h = auth.hash_api_key(salt, raw)
    assert auth.hash_api_key(salt, raw) == h          # deterministic
    assert auth.hash_api_key(salt, raw + "x") != h    # wrong key
    assert auth.hash_api_key(auth.new_salt(), raw) != h  # wrong salt
    assert raw not in h                                # hash is not the key


def test_jwt_issue_and_verify_roundtrip():
    p = auth.Principal(tenant="acme", role="reviewer", sub="k_abc")
    token = auth.issue_jwt(p, "secret", 1800)
    out = auth.verify_jwt(token, "secret")
    assert (out.tenant, out.role, out.sub) == ("acme", "reviewer", "k_abc")


def test_jwt_tampered_token_rejected():
    p = auth.Principal(tenant="acme", role="reviewer", sub="k_abc")
    token = auth.issue_jwt(p, "secret", 1800)
    with pytest.raises(jwt.InvalidTokenError):
        auth.verify_jwt(token, "different-secret")


def test_jwt_expired_token_rejected():
    p = auth.Principal(tenant="acme", role="viewer", sub="k_abc")
    token = auth.issue_jwt(p, "secret", ttl_seconds=10, now=time.time() - 10_000)
    with pytest.raises(jwt.ExpiredSignatureError):
        auth.verify_jwt(token, "secret")


def test_jwt_missing_claim_rejected():
    # A validly-signed token missing a required claim must raise InvalidTokenError, not KeyError.
    now = int(time.time())
    token = jwt.encode({"sub": "k", "tenant": "acme", "iat": now, "exp": now + 100},
                       "secret", algorithm="HS256")  # note: no "role" claim
    with pytest.raises(jwt.InvalidTokenError):
        auth.verify_jwt(token, "secret")
