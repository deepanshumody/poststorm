import pytest

from backend.ledger import db as ledger_db
from backend.ledger import service
from backend.ledger.models import Feedback, PostedLine, ReviewException
from backend.reconcile import reconcile
from backend.schema import Confidence, EventType, LineItem
from tests._auth import authed_client


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


@pytest.fixture(autouse=True)
def clean_iso_tenants():
    """Remove rows for the two isolation tenants before each test."""
    s = ledger_db.SessionLocal()
    try:
        for model in (ReviewException, PostedLine, Feedback):
            s.query(model).filter(model.tenant_id.in_(("iso_a", "iso_b"))).delete(
                synchronize_session=False)
        s.commit()
    finally:
        s.close()
    yield


def _seed_ambiguous(tenant: str):
    s = ledger_db.SessionLocal()
    try:
        a = _li(claim_id="IA1", patient_ref="P-A", paid=50.0, check_number="ISO")
        b = _li(claim_id="IA2", patient_ref="P-B", paid=50.0, check_number="ISO")
        take = _li(claim_id="IA3", patient_ref="P-C", paid=-50.0, check_number="ISO",
                   event_type=EventType.recoup, recoup_flag=True)
        service.post(s, tenant, "iso", [a, b, take], reconcile([a, b, take]).recoups)
    finally:
        s.close()


def test_no_token_is_401():
    c = authed_client()
    c.headers.pop("Authorization")
    assert c.get("/ledger/balances").status_code == 401


def test_viewer_cannot_resolve_403():
    _seed_ambiguous("iso_a")
    viewer = authed_client(role="viewer", tenant="iso_a")
    item = viewer.get("/review/queue").json()["items"][0]
    assert viewer.post(f"/review/{item['id']}/resolve", json={"action": "dismiss"}).status_code == 403


def test_tenant_b_cannot_see_tenant_a_queue():
    _seed_ambiguous("iso_a")
    b = authed_client(role="reviewer", tenant="iso_b")
    assert b.get("/review/queue").json()["items"] == []


def test_tenant_b_resolving_tenant_a_exception_is_404():
    _seed_ambiguous("iso_a")
    a = authed_client(role="reviewer", tenant="iso_a")
    exc_id = a.get("/review/queue").json()["items"][0]["id"]
    b = authed_client(role="reviewer", tenant="iso_b")
    assert b.post(f"/review/{exc_id}/resolve", json={"action": "dismiss"}).status_code == 404


def test_reviewer_identity_lands_in_resolution():
    _seed_ambiguous("iso_a")
    a = authed_client(role="reviewer", tenant="iso_a", sub="k_realreviewer")
    item = a.get("/review/queue").json()["items"][0]
    out = a.post(f"/review/{item['id']}/resolve", json={"action": "pick", "chosen_claim": "IA1"})
    assert out.status_code == 200 and out.json()["posted"] is True
    # The ledger event's reviewer provenance is the authenticated principal, not "demo-reviewer".
    audit = a.get("/ledger/audit?limit=5").json()["events"]
    recoup = next(e for e in audit if e["type"] == "recoup")
    assert recoup["meta"]["reviewer"] == "k_realreviewer"


def test_invalid_corrected_still_400_not_404():
    s = ledger_db.SessionLocal()
    try:
        low = _li(claim_id="ILC", paid=10.0, confidence=Confidence.low, check_number="ISO")
        service.post(s, "iso_a", "iso_lc", [low], [])
    finally:
        s.close()
    a = authed_client(role="reviewer", tenant="iso_a")
    item = a.get("/review/queue").json()["items"][0]
    r = a.post(f"/review/{item['id']}/resolve", json={"action": "correct", "corrected": {"paid": "abc"}})
    assert r.status_code == 400
