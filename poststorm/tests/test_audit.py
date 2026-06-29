from backend.ledger import db as ledger_db
from backend.ledger.models import AuditLog
from tests._auth import authed_client


def _count(action: str, tenant: str = "demo") -> int:
    s = ledger_db.SessionLocal()
    try:
        return s.query(AuditLog).filter_by(action=action, tenant_id=tenant).count()
    finally:
        s.close()


def test_token_issuance_is_audited():
    before = _count("auth.token", tenant="")
    authed_client().post("/auth/token", json={"api_key": "bad"})  # even failures are audited
    assert _count("auth.token", tenant="") == before + 1


def test_mutating_request_writes_audit_row():
    rc = authed_client(role="reviewer", tenant="demo", sub="k_aud")
    before = _count("job.create")
    r = rc.post("/jobs", json={"count": 1})
    assert r.status_code == 200
    assert _count("job.create") == before + 1


def test_read_request_is_not_audited():
    rc = authed_client(role="viewer")
    before_total = _total()
    rc.get("/ledger/balances")
    assert _total() == before_total  # GET reads are not audited


def _total() -> int:
    s = ledger_db.SessionLocal()
    try:
        return s.query(AuditLog).count()
    finally:
        s.close()


def test_admin_audit_endpoint_returns_rows_for_tenant():
    admin = authed_client(role="admin", tenant="demo", sub="k_admin")
    admin.post("/jobs", json={"count": 1})
    r = admin.get("/admin/audit")
    assert r.status_code == 200
    actions = [e["action"] for e in r.json()["events"]]
    assert "job.create" in actions


def test_admin_audit_forbidden_for_reviewer():
    rc = authed_client(role="reviewer")
    assert rc.get("/admin/audit").status_code == 403
