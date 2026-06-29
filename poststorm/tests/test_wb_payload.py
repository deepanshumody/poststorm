from backend.ledger import service
from backend.ledger.db import make_memory_session
from backend.ledger.models import Event
from backend.reconcile import reconcile
from backend.schema import Confidence, EventType, LineItem
from backend.writeback import payload


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def test_idempotency_key_is_deterministic_and_destination_specific():
    a = payload.idempotency_key("demo", 7, "file")
    assert a == payload.idempotency_key("demo", 7, "file") and len(a) == 32
    assert a != payload.idempotency_key("demo", 7, "webhook")
    assert a != payload.idempotency_key("demo", 8, "file")


def test_build_posting_from_a_payment_event():
    s = make_memory_session()
    line = _li(claim_id="C1", patient_ref="P-A", paid=50.0, payer="Aetna")
    service.post(s, "demo", "b1", [line], [])
    ev = s.query(Event).filter_by(type="payment").one()
    p = payload.build_posting(s, ev, "file")
    assert p["type"] == "payment" and p["tenant"] == "demo" and p["payer"] == "Aetna"
    assert p["patient_ref"] == "P-A" and p["claim_id"] == "C1"
    assert p["amount_cents"] == 5000 and p["idempotency_key"] == payload.idempotency_key("demo", ev.id, "file")
    assert any(e["account_type"] == "claim" and e["account_key"] == "C1" for e in p["entries"])


def test_build_posting_from_a_recoup_carries_offset_and_check():
    s = make_memory_session()
    a = _li(claim_id="C1", patient_ref="P-A", paid=50.0, check_number="CK")
    b = _li(claim_id="C2", patient_ref="P-B", paid=100.0, check_number="CK")
    take = _li(claim_id="C3", patient_ref="P-C", paid=-50.0, check_number="CK",
               event_type=EventType.recoup, recoup_flag=True)
    service.post(s, "demo", "b1", [a, b, take], reconcile([a, b, take]).recoups)
    ev = s.query(Event).filter_by(type="recoup").one()
    p = payload.build_posting(s, ev, "file")
    assert p["type"] == "recoup" and p["check_number"] == "CK"
    assert p["offset_original_claim"] in ("C1", "C2") and p["amount_cents"] == 5000


def test_to_835_is_labeled_representative_and_has_segments():
    p = {"idempotency_key": "k", "type": "recoup", "amount_cents": 5000, "payer": "Aetna",
         "patient_ref": "P-C", "claim_id": "C3", "check_number": "CK", "offset_original_claim": "C1"}
    txt = payload.to_835(p)
    assert "representative" in txt.splitlines()[0].lower()
    assert "BPR*" in txt and "CLP*" in txt and "PLB*" in txt  # PLB = provider-level adjustment (recoup)
