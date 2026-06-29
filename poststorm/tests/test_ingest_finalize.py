import json

from backend.ingest import queue as q
from backend.ingest.models import Document, Extraction, IngestJob
from backend.ledger.db import make_memory_session
from backend.ledger.models import Event, ReviewException
from backend.schema import Confidence, EventType, LineItem


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def _extracted_doc(s, job_id, tenant, doc_id, items):
    s.add(Document(id=doc_id, tenant_id=tenant, job_id=job_id, filename=f"{doc_id}.png",
                   content_type="image/png", storage_path="/tmp/x.png", status="extracted"))
    s.add(Extraction(document_id=doc_id, tenant_id=tenant,
                     line_items_json=json.dumps([li.model_dump(mode="json") for li in items])))
    s.commit()


def test_finalize_posts_when_all_terminal_and_is_idempotent():
    s = make_memory_session()
    s.add(IngestJob(id="j_1", tenant_id="demo", status="processing", doc_count=1))
    _extracted_doc(s, "j_1", "demo", "d_1", [_li(claim_id="P1", paid=50.0)])
    pr = q.maybe_finalize_job(s, "j_1")
    assert pr is not None and pr.posted == 1
    assert s.get(IngestJob, "j_1").status == "finalized"
    events_after_first = s.query(Event).filter_by(tenant_id="demo").count()
    # second finalize is a no-op — no double-post
    assert q.maybe_finalize_job(s, "j_1") is None
    assert s.query(Event).filter_by(tenant_id="demo").count() == events_after_first


def test_finalize_waits_for_in_flight_docs():
    s = make_memory_session()
    s.add(IngestJob(id="j_2", tenant_id="demo", status="processing", doc_count=2))
    _extracted_doc(s, "j_2", "demo", "d_a", [_li(claim_id="P1", paid=50.0)])
    s.add(Document(id="d_b", tenant_id="demo", job_id="j_2", filename="b.png",
                   content_type="image/png", storage_path="/tmp/b.png", status="processing"))
    s.commit()
    assert q.maybe_finalize_job(s, "j_2") is None  # one doc still processing
    assert s.get(IngestJob, "j_2").status == "processing"


def test_partial_failure_finalizes_subset_and_recoup_goes_to_review():
    s = make_memory_session()
    s.add(IngestJob(id="j_3", tenant_id="demo", status="processing", doc_count=2))
    # extracted doc carries a cross-patient recoup whose offsetting payment lived in the FAILED doc
    recoup = _li(claim_id="R1", patient_ref="P-R", paid=-50.0, check_number="CK",
                 event_type=EventType.recoup, recoup_flag=True)
    _extracted_doc(s, "j_3", "demo", "d_ok", [recoup])
    s.add(Document(id="d_bad", tenant_id="demo", job_id="j_3", filename="bad.png",
                   content_type="image/png", storage_path="/tmp/bad.png", status="failed",
                   error="extraction_failed"))
    s.commit()
    pr = q.maybe_finalize_job(s, "j_3")
    assert pr is not None
    assert s.get(IngestJob, "j_3").status == "partially_failed"
    # unmatched recoup routes to the D review queue
    assert s.query(ReviewException).filter_by(tenant_id="demo").count() >= 1
