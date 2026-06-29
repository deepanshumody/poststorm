import time

import pytest
from fastapi.testclient import TestClient

from backend.extract import ExtractionResult
from backend.ingest import queue as q
from backend.ingest.models import Document, Extraction, IngestJob
from backend.ledger import db as ledger_db
from backend.main import app
from backend.schema import Confidence, EventType, LineItem
from tests._auth import bearer

_LIFESPAN_DOC_IDS = ("d_lsp", "d_nobg")


@pytest.fixture(autouse=True)
def _clean_lifespan_docs():
    """Delete leftover rows from previous runs so tests are idempotent."""
    s = ledger_db.SessionLocal()
    try:
        for doc_id in _LIFESPAN_DOC_IDS:
            s.query(Extraction).filter_by(document_id=doc_id).delete()
            doc = s.get(Document, doc_id)
            if doc:
                job_id = doc.job_id
                s.delete(doc)
                s.flush()
                job = s.get(IngestJob, job_id)
                if job:
                    s.delete(job)
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


def test_lifespan_workers_drain_the_queue(monkeypatch):
    monkeypatch.setattr("backend.images.load_page_images", lambda p: ["img"])
    monkeypatch.setattr("backend.images.image_to_data_uri", lambda img, **kw: "data:image/png;base64,AA==")
    monkeypatch.setattr("backend.extract.extract_page",
                        lambda uri, **kw: ExtractionResult([_li(claim_id="LSP1", paid=50.0)],
                                                           {}, {}, {}, 5.0))
    s = ledger_db.SessionLocal()
    jid = q.enqueue_job(s, "demo", [q.DocSpec("d_lsp", "f.png", "image/png", "/tmp/f.png")])
    s.close()

    # `with TestClient(app)` runs the lifespan → starts the workers, which drain the queue.
    with TestClient(app) as c:
        deadline = time.time() + 10
        status = None
        while time.time() < deadline:
            r = c.get(f"/ingest/jobs/{jid}", headers={"Authorization": f"Bearer {bearer(role='viewer')}"})
            if r.status_code == 200:
                status = r.json()["status"]
                if status in ("finalized", "partially_failed"):
                    break
            time.sleep(0.2)
    assert status == "finalized"


def test_bare_testclient_starts_no_workers(monkeypatch):
    # Hermetic property: a bare TestClient(app) (no `with`) must NOT run the lifespan,
    # so the ingest workers — and the recover_orphans call that precedes them — never start.
    # Spying on recover_orphans is robust even when a dev server shares the SQLite file.
    calls = []
    monkeypatch.setattr("backend.ingest.worker.recover_orphans", lambda s: calls.append(1))
    _ = TestClient(app)            # no `with` → lifespan does not run
    time.sleep(0.2)
    assert calls == []            # lifespan never started → no workers spawned
