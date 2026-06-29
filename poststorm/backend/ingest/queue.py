import json
import uuid
from dataclasses import dataclass

from backend.ingest.models import Document, Extraction, IngestJob
from backend.ledger.models import _now


@dataclass
class DocSpec:
    doc_id: str
    filename: str
    content_type: str
    storage_path: str


def enqueue_job(session, tenant_id: str, docs: list[DocSpec]) -> str:
    job_id = "j_" + uuid.uuid4().hex[:12]
    session.add(IngestJob(id=job_id, tenant_id=tenant_id, status="pending", doc_count=len(docs)))
    for d in docs:
        session.add(Document(id=d.doc_id, tenant_id=tenant_id, job_id=job_id, filename=d.filename,
                             content_type=d.content_type, storage_path=d.storage_path, status="pending"))
    session.commit()
    return job_id


def claim_next(session, tenant_id: str | None = None) -> Document | None:
    q = session.query(Document.id).filter_by(status="pending")
    if tenant_id is not None:
        q = q.filter_by(tenant_id=tenant_id)
    row = q.order_by(Document.created_at, Document.id).first()
    if row is None:
        return None
    doc_id = row[0]
    # Predicate-guarded transition: only the worker that flips pending→processing wins.
    updated = (session.query(Document)
               .filter_by(id=doc_id, status="pending")
               .update({"status": "processing", "attempts": Document.attempts + 1, "updated_at": _now()}))
    session.commit()
    if updated == 0:
        return None  # another worker won the race; caller retries on the next tick
    return session.get(Document, doc_id)


def record_extraction(session, document, items, model: str, usage: dict, wall_ms: float) -> None:
    session.add(Extraction(
        document_id=document.id, tenant_id=document.tenant_id,
        line_items_json=json.dumps([li.model_dump(mode="json") for li in items]),
        model=model, usage_json=json.dumps(usage or {}), wall_ms=int(wall_ms)))
    document.status = "extracted"
    document.updated_at = _now()
    session.commit()


def mark_failed(session, document, error: str, max_attempts: int) -> None:
    if document.attempts >= max_attempts:
        document.status = "failed"
        document.error = (error or "extraction_failed")[:500]
    else:
        document.status = "pending"  # reset for another attempt
    document.updated_at = _now()
    session.commit()
