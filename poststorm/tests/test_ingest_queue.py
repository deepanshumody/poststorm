from backend.ingest import queue as q
from backend.ingest.models import Document, Extraction, IngestJob
from backend.ledger.db import make_memory_session
from backend.schema import Confidence, EventType, LineItem


def _spec(i):
    return q.DocSpec(doc_id=f"d_{i}", filename=f"f{i}.png", content_type="image/png",
                     storage_path=f"/tmp/f{i}.png")


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def test_enqueue_creates_job_and_pending_docs():
    s = make_memory_session()
    jid = q.enqueue_job(s, "demo", [_spec(1), _spec(2)])
    job = s.get(IngestJob, jid)
    assert job.doc_count == 2 and job.status == "pending"
    assert s.query(Document).filter_by(job_id=jid, status="pending").count() == 2


def test_claim_next_transitions_one_doc_and_never_double_claims():
    s = make_memory_session()
    q.enqueue_job(s, "demo", [_spec(1)])
    first = q.claim_next(s, "demo")
    assert first is not None and first.status == "processing" and first.attempts == 1
    # only one pending doc existed → a second claim returns None, never the same row again
    assert q.claim_next(s, "demo") is None


def test_claim_next_is_tenant_scoped():
    s = make_memory_session()
    q.enqueue_job(s, "ten_a", [_spec(1)])
    assert q.claim_next(s, "ten_b") is None  # other tenant sees nothing
    assert q.claim_next(s, "ten_a") is not None


def test_record_extraction_writes_rows_and_marks_extracted():
    s = make_memory_session()
    q.enqueue_job(s, "demo", [_spec(1)])
    doc = q.claim_next(s, "demo")
    q.record_extraction(s, doc, [_li(), _li(claim_id="C2")], "gemma-4-31b", {"completion_tokens": 5}, 123.4)
    assert s.get(Document, doc.id).status == "extracted"
    ex = s.query(Extraction).filter_by(document_id=doc.id).one()
    import json
    assert len(json.loads(ex.line_items_json)) == 2 and ex.wall_ms == 123


def test_mark_failed_retries_until_max_then_fails():
    s = make_memory_session()
    q.enqueue_job(s, "demo", [_spec(1)])
    doc = q.claim_next(s, "demo")           # attempts == 1
    q.mark_failed(s, doc, "extraction_failed", max_attempts=3)
    assert s.get(Document, doc.id).status == "pending"   # 1 < 3 → retry
    doc = q.claim_next(s, "demo")           # attempts == 2
    q.mark_failed(s, doc, "extraction_failed", max_attempts=3)
    assert s.get(Document, doc.id).status == "pending"   # 2 < 3 → retry
    doc = q.claim_next(s, "demo")           # attempts == 3
    q.mark_failed(s, doc, "extraction_failed", max_attempts=3)
    failed = s.get(Document, doc.id)
    assert failed.status == "failed" and failed.error == "extraction_failed"
