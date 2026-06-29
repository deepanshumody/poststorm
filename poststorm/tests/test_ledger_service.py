from backend.ledger import service
from backend.ledger.db import make_memory_session
from backend.ledger.models import Account, Entry, Event, PostedLine, ReviewException
from backend.reconcile import reconcile
from backend.schema import Confidence, EventType, LineItem


def _li(**kw):
    base = dict(claim_id="C1", payer="Aetna", patient_ref="A", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def test_payment_posts_balanced_double_entry():
    s = make_memory_session()
    line = _li(claim_id="C1", paid=80.0)
    res = service.post(s, "demo", "b1", [line], [])
    assert res.posted == 1 and res.events == 1
    entries = s.query(Entry).all()
    assert len(entries) == 2
    assert sum(e.amount_cents for e in entries if e.direction == "debit") == \
           sum(e.amount_cents for e in entries if e.direction == "credit") == 8000
    claim = s.query(Account).filter_by(type="claim", key="C1").one()
    cash = s.query(Account).filter_by(type="provider_cash", key="main").one()
    assert claim.balance_cents == 8000     # credit
    assert cash.balance_cents == -8000     # debit


def test_cross_patient_recoup_posts_to_dump_account():
    s = make_memory_session()
    pay = _li(claim_id="C1", patient_ref="A", paid=842.50, check_number="CHK7")
    take = _li(claim_id="R9", patient_ref="B", paid=-842.50, check_number="CHK7",
               event_type=EventType.recoup, recoup_flag=True)
    rr = reconcile([pay, take])
    res = service.post(s, "demo", "b1", [pay, take], rr.recoups)
    assert res.posted == 2 and res.exceptions == 0
    dump = s.query(Account).filter_by(type="dump_account", key="CHK7").one()
    rclaim = s.query(Account).filter_by(type="claim", key="R9").one()
    assert dump.balance_cents == 84250        # credit (parked offset)
    assert rclaim.balance_cents == -84250     # debit (prior payment reversed)
    assert res.dump_exposure_cents == 84250
    # every event balances
    for ev in s.query(Event).all():
        es = s.query(Entry).filter_by(event_id=ev.id).all()
        assert sum(e.amount_cents for e in es if e.direction == "debit") == \
               sum(e.amount_cents for e in es if e.direction == "credit")


def test_unmatched_recoup_becomes_exception():
    s = make_memory_session()
    take = _li(claim_id="R9", patient_ref="B", paid=-50.0, check_number="CHKX",
               event_type=EventType.recoup, recoup_flag=True)
    res = service.post(s, "demo", "b1", [take], [])  # no matching payment -> no Recoup
    assert res.exceptions == 1 and res.posted == 0
    assert s.query(ReviewException).count() == 1
    assert s.query(Entry).count() == 0
    assert s.query(PostedLine).filter_by(event_id=None).count() == 1


def test_same_patient_reversal_posts():
    s = make_memory_session()
    pay = _li(claim_id="C1", patient_ref="A", paid=100.0, check_number="CHK1")
    rev = _li(claim_id="C1", patient_ref="A", paid=-100.0, check_number="CHK2",
              event_type=EventType.reversal, recoup_flag=True)
    rr = reconcile([pay, rev])
    res = service.post(s, "demo", "b1", [pay, rev], rr.recoups)
    assert res.posted == 2 and res.exceptions == 0
    claim = s.query(Account).filter_by(type="claim", key="C1").one()
    cash = s.query(Account).filter_by(type="provider_cash", key="main").one()
    assert claim.balance_cents == 0   # +100 payment credit, -100 reversal debit
    assert cash.balance_cents == 0    # -100 payment debit, +100 reversal credit


def test_idempotent_repost_skips():
    s = make_memory_session()
    lines = [_li(claim_id="C1", paid=80.0)]
    service.post(s, "demo", "b1", lines, [])
    res2 = service.post(s, "demo", "b2", lines, [])  # different batch, same line
    assert res2.posted == 0 and res2.skipped == 1
    assert s.query(Event).count() == 1
    assert s.query(PostedLine).count() == 1
