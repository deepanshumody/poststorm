import json

from backend.ledger import review, service
from backend.ledger.db import make_memory_session
from backend.ledger.models import Account, Event, Feedback
from backend.reconcile import reconcile
from backend.schema import Confidence, EventType, LineItem


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def _seed_ambiguous(s):
    a = _li(claim_id="C1", patient_ref="P-A", paid=50.0, check_number="CHK7")
    b = _li(claim_id="C2", patient_ref="P-B", paid=50.0, check_number="CHK7")
    take = _li(claim_id="C3", patient_ref="P-C", paid=-50.0, check_number="CHK7",
               event_type=EventType.recoup, recoup_flag=True)
    service.post(s, "demo", "b1", [a, b, take], reconcile([a, b, take]).recoups)


def test_queue_lists_open_exceptions():
    s = make_memory_session()
    _seed_ambiguous(s)
    q = review.review_queue(s, "demo")
    assert len(q) == 1 and q[0]["kind"] == "ambiguous"
    assert set(q[0]["candidates"]) == {"C1", "C2"}


def test_pick_posts_to_chosen_claim_and_resolves():
    s = make_memory_session()
    _seed_ambiguous(s)
    exc_id = review.review_queue(s, "demo")[0]["id"]
    out = review.resolve(s, "demo", exc_id, "pick", chosen_claim="C1")
    assert out["posted"] is True
    ev = s.get(Event, out["event_id"])
    assert json.loads(ev.meta)["offset_original_claim"] == "C1"
    assert s.query(Account).filter_by(type="dump_account", key="CHK7").one().balance_cents == 5000
    assert review.review_queue(s, "demo") == []  # no longer open
    # idempotent
    assert review.resolve(s, "demo", exc_id, "pick", chosen_claim="C1").get("noop") is True


def test_correct_posts_corrected_line_and_writes_feedback():
    s = make_memory_session()
    low = _li(claim_id="C9", paid=10.0, confidence=Confidence.low)
    service.post(s, "demo", "b1", [low], [])
    exc_id = review.review_queue(s, "demo")[0]["id"]
    out = review.resolve(s, "demo", exc_id, "correct", corrected={"paid": 95.0})
    assert out["posted"] is True
    assert s.query(Account).filter_by(type="claim", key="C9").one().balance_cents == 9500
    fb = s.query(Feedback).one()
    assert json.loads(fb.original_line)["paid"] == 10.0 and json.loads(fb.corrected_line)["paid"] == 95.0


def test_dismiss_posts_nothing():
    s = make_memory_session()
    low = _li(claim_id="C9", paid=10.0, confidence=Confidence.low)
    service.post(s, "demo", "b1", [low], [])
    exc_id = review.review_queue(s, "demo")[0]["id"]
    out = review.resolve(s, "demo", exc_id, "dismiss")
    assert out["posted"] is False
    assert s.query(Event).count() == 0
    assert review.review_queue(s, "demo", status="dismissed")[0]["id"] == exc_id


def test_pick_without_chosen_claim_raises():
    s = make_memory_session()
    _seed_ambiguous(s)
    exc_id = review.review_queue(s, "demo")[0]["id"]
    import pytest
    with pytest.raises(ValueError):
        review.resolve(s, "demo", exc_id, "pick")
