from tests._auth import authed_client

admin = authed_client(role="admin", tenant="demo", sub="k_admin")
client = authed_client(role="reviewer")  # non-admin


def test_onboard_tenant_returns_working_key():
    r = admin.post("/admin/tenants", json={"tenant_id": "acme", "name": "Acme", "role": "reviewer"})
    assert r.status_code == 200
    raw = r.json()["api_key"]
    assert raw.startswith("pk_acme_")
    # the issued key exchanges for a JWT scoped to acme/reviewer
    tok = client.post("/auth/token", json={"api_key": raw})
    assert tok.status_code == 200
    import jwt as _jwt

    from backend.config import get_settings
    claims = _jwt.decode(tok.json()["access_token"], get_settings().jwt_secret, algorithms=["HS256"])
    assert claims["tenant"] == "acme" and claims["role"] == "reviewer"


def test_onboard_requires_admin():
    assert client.post("/admin/tenants", json={"tenant_id": "x"}).status_code == 403


def test_rotate_issues_second_working_key():
    onboard = admin.post("/admin/tenants", json={"tenant_id": "rot", "role": "viewer"})
    raw1 = onboard.json()["api_key"]
    r = admin.post("/admin/tenants/rot/keys", json={"role": "viewer"})
    assert r.status_code == 200
    raw2 = r.json()["api_key"]
    assert raw1 != raw2
    # rotation is non-destructive: BOTH the old and the new key exchange for a token
    assert client.post("/auth/token", json={"api_key": raw1}).status_code == 200
    assert client.post("/auth/token", json={"api_key": raw2}).status_code == 200


def test_revoke_disables_key():
    r = admin.post("/admin/tenants", json={"tenant_id": "rev", "role": "viewer"})
    raw = r.json()["api_key"]
    kid = r.json()["kid"]
    assert client.post("/auth/token", json={"api_key": raw}).status_code == 200
    assert admin.delete(f"/admin/keys/{kid}").status_code == 200
    assert client.post("/auth/token", json={"api_key": raw}).status_code == 401
