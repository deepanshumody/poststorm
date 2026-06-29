"""Test helper: a TestClient carrying a valid Bearer token for a given tenant/role."""
from fastapi.testclient import TestClient

from backend import auth
from backend.config import get_settings
from backend.main import app


def bearer(role: str = "reviewer", tenant: str = "demo", sub: str = "test-kid") -> str:
    s = get_settings()
    principal = auth.Principal(tenant=tenant, role=role, sub=sub)
    return auth.issue_jwt(principal, s.jwt_secret, s.jwt_ttl_seconds)


def authed_client(role: str = "reviewer", tenant: str = "demo", sub: str = "test-kid") -> TestClient:
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {bearer(role, tenant, sub)}"})
    return c
