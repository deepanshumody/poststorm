import json
import uuid
from dataclasses import dataclass

from backend.ingest.models import Document, Extraction, IngestJob
from backend.ledger import service as ledger_service
from backend.ledger.models import _now
from backend.reconcile import reconcile
from backend.schema import LineItem


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


def _job_line_items(session, job_id: str, tenant_id: str) -> list:
    items: list = []
    docs = (session.query(Document)
            .filter_by(job_id=job_id, tenant_id=tenant_id, status="extracted").all())
    for d in docs:
        ex = (session.query(Extraction).filter_by(document_id=d.id)
              .order_by(Extraction.id.desc()).first())
        if ex is None:
            continue
        items.extend(LineItem(**raw) for raw in json.loads(ex.line_items_json))
    return items


def maybe_finalize_job(session, job_id: str):
    job = session.get(IngestJob, job_id)
    if job is None or job.status in ("finalized", "partially_failed"):
        return None  # idempotent: already terminal
    docs = session.query(Document).filter_by(job_id=job_id).all()
    if not docs or any(d.status in ("pending", "processing") for d in docs):
        return None  # not all documents are terminal yet
    items = _job_line_items(session, job_id, job.tenant_id)
    rr = reconcile(items)
    pr = ledger_service.post(session, job.tenant_id, job_id, items, rr.recoups)
    any_failed = any(d.status == "failed" for d in docs)
    job.status = "partially_failed" if any_failed else "finalized"
    job.finalized_at = _now()
    job.post_summary = json.dumps({"posted": pr.posted, "skipped": pr.skipped,
                                   "exceptions": pr.exceptions, "events": pr.events,
                                   "dump_exposure_cents": pr.dump_exposure_cents})
    session.commit()
    return pr
