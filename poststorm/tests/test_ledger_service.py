from backend.ledger import service
from backend.ledger.db import make_memory_session
from backend.ledger.models import Account, Entry, Event, PostedLine
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


def test_idempotent_repost_skips():
    s = make_memory_session()
    lines = [_li(claim_id="C1", paid=80.0)]
    service.post(s, "demo", "b1", lines, [])
    res2 = service.post(s, "demo", "b2", lines, [])  # different batch, same line
    assert res2.posted == 0 and res2.skipped == 1
    assert s.query(Event).count() == 1
    assert s.query(PostedLine).count() == 1
