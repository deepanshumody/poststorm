from types import SimpleNamespace

from backend.eval import score
from backend.reconcile import Recoup
from backend.schema import Confidence, EventType, LineItem


def _rr(*recoups):
    return SimpleNamespace(recoups=list(recoups))


def _r(claim, status, orig="P1"):
    return Recoup(recoup_claim_id=claim, original_claim_id=orig, cross_patient=True,
                  dump_account_id="dump_CK", amount=50.0, status=status)


def test_recoup_perfect_recall_and_precision():
    m = score.recoup_metrics(_rr(_r("R1", "matched"), _r("R2", "matched")), {"R1", "R2"})
    assert m["planted"] == 2 and m["caught"] == 2 and m["recall"] == 1.0 and m["precision"] == 1.0


def test_recoup_missed_drops_recall():
    m = score.recoup_metrics(_rr(_r("R1", "matched")), {"R1", "R2"})
    assert m["caught"] == 1 and m["missed"] == 1 and m["recall"] == 0.5


def test_recoup_false_positive_drops_precision():
    m = score.recoup_metrics(_rr(_r("R1", "matched"), _r("RX", "matched")), {"R1"})
    assert m["false_positives"] == 1 and m["precision"] == 0.5 and m["recall"] == 1.0


def test_needs_review_counts_as_flagged_detection():
    m = score.recoup_metrics(_rr(_r("R1", "needs_review")), {"R1"})
    assert m["needs_review"] == 1 and m["caught"] == 0 and m["recall"] == 1.0 and m["missed"] == 0


def _li(conf, **kw):
    base = dict(claim_id="C1", payer="Aetna", patient_ref="P-A", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=conf, source_span="x")
    base.update(kw)
    return LineItem(**base)


def _t(**kw):
    base = dict(claim_id="C1", payer="Aetna", patient_ref="P-A", charge=100.0, allowed=80.0,
                paid=80.0, adjustment=20.0, carc=None, recoup_flag=False, event_type="payment")
    base.update(kw)
    return base


def test_confidence_calibration_low_carries_errors():
    ext = {"d": [_li(Confidence.low, claim_id="C1", paid=99.0),   # low + error
                 _li(Confidence.high, claim_id="C2", paid=80.0)]}  # high + correct
    truth = {"d": {"lines": [_t(claim_id="C1", paid=80.0), _t(claim_id="C2", paid=80.0)]}}
    cal = score.confidence_calibration(ext, truth)
    assert cal["low_count"] == 1 and cal["high_count"] == 1
    assert cal["low_error_rate"] == 1.0 and cal["high_error_rate"] == 0.0


def test_build_report_shape():
    ext = {"d": [_li(Confidence.high, claim_id="C1", paid=80.0)]}
    truth = {"d": {"lines": [_t(claim_id="C1", paid=80.0)]}}
    rep = score.build_report(ext, truth, _rr(_r("R1", "matched")), {"R1"}, "gemma-4-31b", "2026-06-30T00:00:00+00:00")
    assert rep["model"] == "gemma-4-31b" and rep["docs"] == 1
    assert rep["field_accuracy"]["overall"] == 1.0
    assert rep["recoup"]["recall"] == 1.0 and rep["line_match"]["matched"] == 1
    assert set(rep["confidence"]) == {"low_count", "high_count", "low_error_rate", "high_error_rate"}
