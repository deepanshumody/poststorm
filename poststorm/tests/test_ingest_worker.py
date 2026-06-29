import pytest

from backend.extract import ExtractionResult
from backend.ingest import queue as q
from backend.ingest import worker
from backend.ingest.models import Document, Extraction, IngestJob
from backend.ledger import db as ledger_db
from backend.schema import Confidence, EventType, LineItem

_WORKER_TEST_TENANTS = ("wk_a", "wk_b", "wk_c")


@pytest.fixture(autouse=True)
def _worker_db():
    ledger_db.init_db()  # ensure tables exist when this file runs in isolation
    s = ledger_db.SessionLocal()
    try:
        for tenant in _WORKER_TEST_TENANTS:
            s.query(Extraction).filter(Extraction.tenant_id == tenant).delete()
            s.query(Document).filter(Document.tenant_id == tenant).delete()
            s.query(IngestJob).filter(IngestJob.tenant_id == tenant).delete()
        s.commit()
    finally:
        s.close()
    yield


def _li(**kw):
    base = dict(claim_id="C", payer="Aetna", patient_ref="P", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")
    base.update(kw)
    return LineItem(**base)


def _clean(s, job_prefix):
    # Worker tests use the real file DB (SessionLocal); isolate by unique tenant.
    pass


def _fake_images(monkeypatch):
    monkeypatch.setattr("backend.images.load_page_images", lambda p: ["img"])
    monkeypatch.setattr("backend.images.image_to_data_uri", lambda img, **kw: "data:image/png;base64,AA==")


def test_process_one_extracts_and_finalizes(monkeypatch):
    _fake_images(monkeypatch)
    monkeypatch.setattr("backend.extract.extract_page",
                        lambda uri, **kw: ExtractionResult([_li(claim_id="WP1", paid=50.0)],
                                                           {}, {"completion_tokens": 3}, {}, 12.0))
    s = ledger_db.SessionLocal()
    jid = q.enqueue_job(s, "wk_a", [q.DocSpec("d_wka", "f.png", "image/png", "/tmp/f.png")])
    s.close()

    assert worker.process_one("wk_a") is True
    s = ledger_db.SessionLocal()
    try:
        assert s.get(Document, "d_wka").status == "extracted"
        assert s.get(IngestJob, jid).status == "finalized"
    finally:
        s.close()


def test_process_one_returns_false_when_empty(monkeypatch):
    assert worker.process_one("wk_empty_tenant_xyz") is False


def test_process_one_marks_failed_on_extract_error(monkeypatch):
    _fake_images(monkeypatch)
    def boom(uri, **kw):
        raise RuntimeError("internal://secret-host/boom")
    monkeypatch.setattr("backend.extract.extract_page", boom)
    s = ledger_db.SessionLocal()
    q.enqueue_job(s, "wk_b", [q.DocSpec("d_wkb", "f.png", "image/png", "/tmp/f.png")])
    s.close()

    # max_attempts=1 → one failure marks it failed
    assert worker.process_one("wk_b", max_attempts=1) is True
    s = ledger_db.SessionLocal()
    try:
        doc = s.get(Document, "d_wkb")
        assert doc.status == "failed" and doc.error == "extraction_failed"  # redacted, no secret host
    finally:
        s.close()


def test_recover_orphans_resets_processing():
    s = ledger_db.SessionLocal()
    try:
        q.enqueue_job(s, "wk_c", [q.DocSpec("d_wkc", "f.png", "image/png", "/tmp/f.png")])
        q.claim_next(s, "wk_c")  # → processing
        assert s.get(Document, "d_wkc").status == "processing"
        worker.recover_orphans(s)
        assert s.get(Document, "d_wkc").status == "pending"
    finally:
        s.close()
