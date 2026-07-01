from backend.eval import score
from backend.schema import Confidence, EventType, LineItem


def _li(**kw):
    base = dict(claim_id="C1", payer="Aetna", patient_ref="P-A", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def _truth(**kw):
    base = dict(claim_id="C1", payer="Aetna", patient_ref="P-A", charge=100.0, allowed=80.0,
                paid=80.0, adjustment=20.0, carc=None, recoup_flag=False, event_type="payment")
    base.update(kw)
    return base


def test_match_doc_lines_pairs_by_claim_id():
    matched, eo, to = score.match_doc_lines([_li(claim_id="C1"), _li(claim_id="C2")],
                                            [_truth(claim_id="C1"), _truth(claim_id="C3")])
    assert len(matched) == 1 and eo == ["C2"] and to == ["C3"]


def test_field_accuracy_perfect_is_one():
    ext = {"d1": [_li(claim_id="C1", paid=80.0)]}
    truth = {"d1": {"lines": [_truth(claim_id="C1", paid=80.0)]}}
    fa = score.field_accuracy(ext, truth)
    assert fa["overall"] == 1.0 and fa["by_field"]["paid"] == 1.0


def test_field_accuracy_wrong_paid_drops_that_field():
    ext = {"d1": [_li(claim_id="C1", paid=99.0)]}
    truth = {"d1": {"lines": [_truth(claim_id="C1", paid=80.0)]}}
    fa = score.field_accuracy(ext, truth)
    assert fa["by_field"]["paid"] == 0.0 and fa["by_field"]["claim_id"] == 1.0 and fa["overall"] < 1.0


def test_money_tolerance_allows_within_cents():
    ext = {"d1": [_li(claim_id="C1", paid=80.004)]}   # rounds to 8000 cents
    truth = {"d1": {"lines": [_truth(claim_id="C1", paid=80.0)]}}
    assert score.field_accuracy(ext, truth)["by_field"]["paid"] == 1.0
