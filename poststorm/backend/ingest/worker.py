import asyncio

from backend import extract, images
from backend.config import get_settings
from backend.ingest import queue as ingest_queue
from backend.ingest.models import Document, IngestJob
from backend.ledger import db as ledger_db
from backend.logging_config import get_logger

log = get_logger("poststorm.ingest.worker")


def process_one(tenant_id: str | None = None, max_attempts: int | None = None) -> bool:
    """Claim and process one pending document. Synchronous (runs in a worker thread).
    Returns True if a document was claimed, False if the queue was empty."""
    s = ledger_db.SessionLocal()
    try:
        doc = ingest_queue.claim_next(s, tenant_id)
        if doc is None:
            return False
        job = s.get(IngestJob, doc.job_id)
        if job is not None and job.status == "pending":
            job.status = "processing"
            s.commit()
        attempts_cap = max_attempts if max_attempts is not None else get_settings().ingest_max_attempts
        try:
            pages = images.load_page_images(doc.storage_path)
            items: list = []
            model, usage, wall = "", {}, 0.0
            for img in pages:
                res = extract.extract_page(images.image_to_data_uri(img, max_dim=1600))
                items.extend(res.line_items)
                model = get_settings().cerebras_model
                usage = res.usage
                wall += res.wall_ms
            doc.page_count = len(pages)
            s.commit()
            ingest_queue.record_extraction(s, doc, items, model, usage, wall)
        except Exception:
            log.exception("extraction failed for document %s", doc.id)  # full trace server-side only
            ingest_queue.mark_failed(s, doc, "extraction_failed", attempts_cap)
        ingest_queue.maybe_finalize_job(s, doc.job_id)
        return True
    finally:
        s.close()


def recover_orphans(session) -> int:
    """Reset documents abandoned mid-flight by a crash so workers re-try them."""
    n = session.query(Document).filter_by(status="processing").update({"status": "pending"})
    session.commit()
    return n


async def worker_loop(stop_event, idle_sleep: float | None = None) -> None:
    idle = idle_sleep if idle_sleep is not None else get_settings().ingest_idle_sleep
    while not stop_event.is_set():
        try:
            did = await asyncio.to_thread(process_one)
        except Exception:
            log.exception("ingest worker iteration failed; continuing")
            did = False
        if not did:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=idle)
            except TimeoutError:
                pass
