import pytest

from backend.ledger import db as ledger_db
from backend.ledger import service
from backend.ledger.models import PostedLine, ReviewException
from backend.reconcile import reconcile
from backend.schema import Confidence, EventType, LineItem
from tests._auth import authed_client

client = authed_client(role="reviewer")


@pytest.fixture(autouse=True)
def isolate_review_tables():
    """Clear review exceptions and placeholder PostedLine rows before each test
    so _seed() can always create fresh exceptions."""
    s = ledger_db.SessionLocal()
    try:
        s.query(ReviewException).delete()
        s.query(PostedLine).filter(PostedLine.event_id.is_(None)).delete()
        s.commit()
    finally:
        s.close()
    yield


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def _seed():
    s = ledger_db.SessionLocal()
    a = _li(claim_id="A1", patient_ref="P-A", paid=50.0, check_number="CK_API")
    b = _li(claim_id="A2", patient_ref="P-B", paid=50.0, check_number="CK_API")
    take = _li(claim_id="A3", patient_ref="P-C", paid=-50.0, check_number="CK_API",
               event_type=EventType.recoup, recoup_flag=True)
    service.post(s, "demo", "api", [a, b, take], reconcile([a, b, take]).recoups)
    s.close()


def test_queue_shape():
    _seed()
    r = client.get("/review/queue")
    assert r.status_code == 200 and "items" in r.json()


def test_resolve_pick_without_chosen_claim_is_400():
    _seed()
    item = client.get("/review/queue").json()["items"][0]
    r = client.post(f"/review/{item['id']}/resolve", json={"action": "pick"})
    assert r.status_code == 400


def test_resolve_dismiss_ok():
    _seed()
    item = client.get("/review/queue").json()["items"][0]
    r = client.post(f"/review/{item['id']}/resolve", json={"action": "dismiss"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"


def _seed_low_confidence():
    """Seed a single low-confidence exception for correct-action tests."""
    s = ledger_db.SessionLocal()
    low = _li(claim_id="LC1", paid=10.0, confidence=Confidence.low)
    service.post(s, "demo", "api_lc", [low], [])
    s.close()


def test_resolve_correct_invalid_corrected_returns_400():
    """Spec §7: POST /review/{id}/resolve with an invalid corrected dict must return 400, not 500."""
    _seed_low_confidence()
    item = client.get("/review/queue").json()["items"][0]
    r = client.post(f"/review/{item['id']}/resolve",
                    json={"action": "correct", "corrected": {"paid": "abc"}})
    assert r.status_code == 400
