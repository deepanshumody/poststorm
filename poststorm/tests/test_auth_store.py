from backend import auth
from backend.config import Settings
from backend.ledger.db import make_memory_session


def test_issue_then_verify_returns_principal():
    s = make_memory_session()
    auth.create_tenant(s, "acme", "Acme")
    kid, raw = auth.issue_key(s, "acme", "reviewer")
    s.commit()
    p = auth.verify_api_key(s, raw)
    assert p is not None
    assert (p.tenant, p.role, p.sub) == ("acme", "reviewer", kid)


def test_unknown_key_returns_none():
    s = make_memory_session()
    auth.create_tenant(s, "acme")
    auth.issue_key(s, "acme", "viewer")
    s.commit()
    assert auth.verify_api_key(s, "pk_acme_not-a-real-key") is None


def test_revoked_key_no_longer_verifies():
    s = make_memory_session()
    auth.create_tenant(s, "acme")
    kid, raw = auth.issue_key(s, "acme", "admin")
    s.commit()
    assert auth.verify_api_key(s, raw) is not None
    assert auth.revoke_key(s, kid) is True
    s.commit()
    assert auth.verify_api_key(s, raw) is None


def test_seed_is_idempotent_and_demo_key_works():
    s = make_memory_session()
    settings = Settings()
    auth.seed_tenants(s, settings)
    auth.seed_tenants(s, settings)  # second call must not duplicate
    raw = auth.demo_api_key(settings)
    p = auth.verify_api_key(s, raw)
    assert p is not None and p.tenant == "demo" and p.role == "reviewer"
    from backend.ledger.models import ApiKey
    assert s.query(ApiKey).filter_by(tenant_id="demo", active=True).count() == 1


def test_admin_bootstrap_key_seeds_admin_role():
    s = make_memory_session()
    settings = Settings(admin_bootstrap_key="pk_admin_bootstrap_test")
    auth.seed_tenants(s, settings)
    auth.seed_tenants(s, settings)  # idempotent
    p = auth.verify_api_key(s, "pk_admin_bootstrap_test")
    assert p is not None and p.tenant == "admin" and p.role == "admin"
    from backend.ledger.models import ApiKey
    assert s.query(ApiKey).filter_by(tenant_id="admin", active=True).count() == 1


def test_no_admin_key_when_bootstrap_unset():
    s = make_memory_session()
    auth.seed_tenants(s, Settings())  # admin_bootstrap_key defaults to ""
    from backend.ledger.models import ApiKey
    assert s.query(ApiKey).filter_by(tenant_id="admin").count() == 0
