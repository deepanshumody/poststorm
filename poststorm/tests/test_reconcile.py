from backend import reconcile
from backend.schema import Confidence, EventType, LineItem


def _li(**kw):
    base = dict(
        claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-04",
        carc=None, rarc=[], charge=200.0, allowed=120.0, paid=120.0, adjustment=80.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x",
    )
    base.update(kw)
    return LineItem(**base)


def test_plain_payment_posts_to_ledger():
    r = reconcile.reconcile([_li(claim_id="C1", patient_ref="P-A")])
    assert not r.recoups
    assert any(e.entity_id == "C1" and e.direction == "credit" for e in r.ledger)


def test_cross_patient_recoup_flagged_as_dump_account():
    pay_a = _li(claim_id="C1", patient_ref="P-A", paid=842.0, check_number="CHK7")
    take_b = _li(claim_id="C2", patient_ref="P-B", paid=-842.0, check_number="CHK7",
                 event_type=EventType.recoup, recoup_flag=True)
    r = reconcile.reconcile([pay_a, take_b])
    rec = [x for x in r.recoups if x.status == "matched"]
    assert len(rec) == 1 and rec[0].cross_patient is True
    assert rec[0].original_claim_id == "C1" and rec[0].dump_account_id
    assert any(e.entity_id == "C2" and e.direction == "debit" for e in r.ledger)
    assert any(e.entity_id == "C1" and e.direction == "credit" for e in r.ledger)


def test_same_patient_reversal_matches():
    pay = _li(claim_id="C1", patient_ref="P-A", paid=100.0, check_number="CHK1")
    rev = _li(claim_id="C1", patient_ref="P-A", paid=-100.0, check_number="CHK2",
              event_type=EventType.reversal, recoup_flag=True)
    r = reconcile.reconcile([pay, rev])
    assert any(x.status == "matched" and x.cross_patient is False for x in r.recoups)


def test_ambiguous_match_goes_to_needs_review():
    a = _li(claim_id="C1", patient_ref="P-A", paid=50.0, check_number="CHK7")
    b = _li(claim_id="C2", patient_ref="P-B", paid=50.0, check_number="CHK7")
    take = _li(claim_id="C3", patient_ref="P-C", paid=-50.0, check_number="CHK7",
               event_type=EventType.recoup, recoup_flag=True)
    r = reconcile.reconcile([a, b, take])
    assert any(x.status == "needs_review" for x in r.recoups)
    assert take in r.needs_review
