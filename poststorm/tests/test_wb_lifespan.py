import time

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.ledger import db as ledger_db
from backend.ledger import service
from backend.main import app
from backend.schema import Confidence, EventType, LineItem
from backend.writeback.models import Delivery


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def _cleanup_wb_life():
    """Delete all rows for the wb_life test tenant so the test is idempotent."""
    s = ledger_db.SessionLocal()
    from backend.ledger.models import Account, Entry, Event, PostedLine
    ev_ids = [e.id for e in s.query(Event).filter_by(tenant_id="wb_life").all()]
    s.query(Delivery).filter_by(tenant_id="wb_life").delete()
    if ev_ids:
        s.query(Entry).filter(Entry.event_id.in_(ev_ids)).delete(synchronize_session=False)
    s.query(PostedLine).filter_by(tenant_id="wb_life").delete()
    s.query(Event).filter_by(tenant_id="wb_life").delete()
    s.query(Account).filter_by(tenant_id="wb_life").delete()
    s.commit()
    s.close()


def test_lifespan_delivers_a_seeded_event_to_file(tmp_path, monkeypatch):
    _cleanup_wb_life()  # ensure clean state from any prior run
    monkeypatch.setattr(get_settings(), "export_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "writeback_destinations", "file")
    s = ledger_db.SessionLocal()
    service.post(s, "wb_life", "b1", [_li(claim_id="WBL1", paid=50.0)], [])
    s.close()

    with TestClient(app):  # runs the lifespan → relay enqueues + worker delivers
        deadline = time.time() + 10
        delivered = False
        while time.time() < deadline:
            s = ledger_db.SessionLocal()
            d = s.query(Delivery).filter_by(tenant_id="wb_life", destination="file").first()
            st = d.status if d else None
            s.close()
            if st == "delivered":
                delivered = True
                break
            time.sleep(0.2)
    assert delivered
    # clean up the lifespan test's rows
    _cleanup_wb_life()
