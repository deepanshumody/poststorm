from fastapi.testclient import TestClient

from backend import auth
from backend.config import get_settings
from backend.main import app

client = TestClient(app)


def test_whoami_without_token_is_401():
    r = client.get("/auth/whoami")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_token_exchange_with_demo_key_then_whoami():
    raw = auth.demo_api_key(get_settings())
    r = client.post("/auth/token", json={"api_key": raw})
    assert r.status_code == 200
    token = r.json()["access_token"]
    who = client.get("/auth/whoami", headers={"Authorization": f"Bearer {token}"})
    assert who.status_code == 200
    body = who.json()
    assert body["tenant"] == "demo" and body["role"] == "reviewer"


def test_token_exchange_with_bad_key_is_401():
    r = client.post("/auth/token", json={"api_key": "pk_demo_not-real"})
    assert r.status_code == 401


def test_demo_token_endpoint_issues_usable_jwt():
    r = client.get("/auth/demo-token")
    assert r.status_code == 200
    token = r.json()["access_token"]
    who = client.get("/auth/whoami", headers={"Authorization": f"Bearer {token}"})
    assert who.status_code == 200 and who.json()["tenant"] == "demo"


def test_expired_or_garbage_token_is_401():
    r = client.get("/auth/whoami", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401
