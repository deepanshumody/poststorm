from backend.ledger import service
from backend.ledger.db import make_memory_session
from backend.schema import Confidence, EventType, LineItem
from backend.writeback import relay
from backend.writeback.models import Delivery


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def test_enqueue_creates_one_delivery_per_event_and_destination():
    s = make_memory_session()
    service.post(s, "demo", "b1", [_li(claim_id="C1", paid=50.0), _li(claim_id="C2", paid=60.0)], [])
    n = relay.enqueue_pending(s, ["file", "webhook"])
    assert n == 4  # 2 events x 2 destinations
    assert s.query(Delivery).filter_by(destination="file", status="pending").count() == 2
    assert s.query(Delivery).filter_by(destination="webhook").count() == 2


def test_enqueue_is_idempotent_on_rerun():
    s = make_memory_session()
    service.post(s, "demo", "b1", [_li(claim_id="C1", paid=50.0)], [])
    assert relay.enqueue_pending(s, ["file"]) == 1
    assert relay.enqueue_pending(s, ["file"]) == 0  # nothing new
    assert s.query(Delivery).filter_by(destination="file").count() == 1
