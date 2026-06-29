import uuid

from backend.ledger import db as ledger_db
from backend.writeback.models import Delivery
from tests._auth import authed_client


def _seed_delivery(tenant, status="dead", dest=None):
    if dest is None:
        dest = f"webhook_{uuid.uuid4().hex[:8]}"
    s = ledger_db.SessionLocal()
    d = Delivery(tenant_id=tenant, event_id=1, destination=dest, status=status,
                 idempotency_key=uuid.uuid4().hex, attempts=5, last_error="webhook_503")
    s.add(d)
    s.commit()
    did = d.id
    s.close()
    return did


def test_deliveries_list_is_tenant_scoped():
    _seed_delivery("wbapi_a")
    a = authed_client(role="viewer", tenant="wbapi_a")
    assert len(a.get("/writeback/deliveries").json()["deliveries"]) >= 1
    b = authed_client(role="viewer", tenant="wbapi_b")
    assert b.get("/writeback/deliveries").json()["deliveries"] == []  # B sees none of A's


def test_retry_resets_a_dead_delivery():
    did = _seed_delivery("wbapi_c", status="dead")
    rc = authed_client(role="reviewer", tenant="wbapi_c")
    r = rc.post(f"/writeback/deliveries/{did}/retry")
    assert r.status_code == 200 and r.json()["status"] == "pending"
    s = ledger_db.SessionLocal()
    try:
        assert s.get(Delivery, did).attempts == 0
    finally:
        s.close()


def test_retry_cross_tenant_is_404():
    did = _seed_delivery("wbapi_d", status="failed")
    other = authed_client(role="reviewer", tenant="wbapi_e")
    assert other.post(f"/writeback/deliveries/{did}/retry").status_code == 404


def test_deliveries_requires_auth():
    a = authed_client(role="viewer", tenant="wbapi_a")
    a.headers.pop("Authorization")
    assert a.get("/writeback/deliveries").status_code == 401
