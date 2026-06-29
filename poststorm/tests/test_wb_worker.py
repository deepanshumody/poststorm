import pytest

from backend.config import get_settings
from backend.ledger import db as ledger_db
from backend.ledger import service
from backend.schema import Confidence, EventType, LineItem
from backend.writeback import adapters, relay, worker
from backend.writeback.models import Delivery


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


_WB_TEST_TENANTS = ("wb_a", "wb_b", "wb_c", "wb_d")


@pytest.fixture(autouse=True)
def _wb_db():
    ledger_db.init_db()  # ensure tables exist when this file runs in isolation
    s = ledger_db.SessionLocal()
    try:
        from backend.ledger.models import Account, Entry, Event, PostedLine
        # Clean up test tenant data (FK-safe order)
        for t in _WB_TEST_TENANTS:
            ev_ids = [e.id for e in s.query(Event).filter_by(tenant_id=t).all()]
            s.query(Delivery).filter(Delivery.tenant_id == t).delete()
            if ev_ids:
                s.query(Entry).filter(Entry.event_id.in_(ev_ids)).delete(synchronize_session=False)
                s.query(PostedLine).filter(PostedLine.event_id.in_(ev_ids)).delete(synchronize_session=False)
            s.query(PostedLine).filter_by(tenant_id=t).delete()
            s.query(Event).filter_by(tenant_id=t).delete()
            s.query(Account).filter_by(tenant_id=t).delete()
        s.commit()
    finally:
        s.close()
    yield


def _seed_one_event(tenant):
    s = ledger_db.SessionLocal()
    try:
        service.post(s, tenant, "b1", [_li(claim_id="C1", paid=50.0)], [])
    finally:
        s.close()


def test_deliver_one_file_writes_and_marks_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "export_dir", str(tmp_path))
    _seed_one_event("wb_a")
    s = ledger_db.SessionLocal()
    relay.enqueue_pending(s, ["file"])
    s.close()
    assert worker.deliver_one("wb_a") is True
    s = ledger_db.SessionLocal()
    try:
        d = s.query(Delivery).filter_by(tenant_id="wb_a", destination="file").one()
        assert d.status == "delivered" and d.delivered_at is not None and d.payload_sha256
    finally:
        s.close()


def test_deliver_one_returns_false_when_empty():
    assert worker.deliver_one("wb_empty_tenant_zzz") is False


def test_webhook_retry_then_dead(monkeypatch):
    monkeypatch.setattr(get_settings(), "writeback_max_attempts", 2)
    monkeypatch.setattr(adapters, "deliver_webhook",
                        lambda posting, settings, client=None:
                        adapters.DeliveryResult(False, True, "webhook_503", ""))
    _seed_one_event("wb_b")
    s = ledger_db.SessionLocal()
    relay.enqueue_pending(s, ["webhook"])
    s.close()
    worker.deliver_one("wb_b")  # attempts 1 → pending
    s = ledger_db.SessionLocal()
    assert s.query(Delivery).filter_by(tenant_id="wb_b").one().status == "pending"
    s.close()
    worker.deliver_one("wb_b")  # attempts 2 → dead
    s = ledger_db.SessionLocal()
    try:
        d = s.query(Delivery).filter_by(tenant_id="wb_b").one()
        assert d.status == "dead" and d.last_error == "webhook_503"
    finally:
        s.close()


def test_webhook_permanent_4xx_fails(monkeypatch):
    monkeypatch.setattr(adapters, "deliver_webhook",
                        lambda posting, settings, client=None:
                        adapters.DeliveryResult(False, False, "webhook_400", ""))
    _seed_one_event("wb_c")
    s = ledger_db.SessionLocal()
    relay.enqueue_pending(s, ["webhook"])
    s.close()
    worker.deliver_one("wb_c")
    s = ledger_db.SessionLocal()
    try:
        assert s.query(Delivery).filter_by(tenant_id="wb_c").one().status == "failed"
    finally:
        s.close()


def test_recover_orphans_resets_delivering():
    _seed_one_event("wb_d")
    s = ledger_db.SessionLocal()
    relay.enqueue_pending(s, ["file"])
    worker.claim_next(s, "wb_d")  # → delivering
    assert s.query(Delivery).filter_by(tenant_id="wb_d").one().status == "delivering"
    worker.recover_orphans(s)
    assert s.query(Delivery).filter_by(tenant_id="wb_d").one().status == "pending"
    s.close()
