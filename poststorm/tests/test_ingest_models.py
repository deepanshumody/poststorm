from backend.ingest.models import Document, Extraction, IngestJob
from backend.ledger.db import make_memory_session


def test_ingest_tables_create_and_roundtrip():
    s = make_memory_session()
    s.add(IngestJob(id="j_1", tenant_id="demo", status="pending", doc_count=1))
    s.add(Document(id="d_1", tenant_id="demo", job_id="j_1", filename="a.png",
                   content_type="image/png", storage_path="/tmp/a.png", status="pending"))
    s.add(Extraction(document_id="d_1", tenant_id="demo", line_items_json="[]", model="gemma-4-31b"))
    s.commit()

    job = s.get(IngestJob, "j_1")
    assert job.status == "pending" and job.doc_count == 1 and job.finalized_at is None
    doc = s.get(Document, "d_1")
    assert doc.status == "pending" and doc.attempts == 0 and doc.error is None
    assert s.query(Extraction).filter_by(document_id="d_1").count() == 1
