import asyncio
import time
from dataclasses import asdict

from backend import extract, images, reconcile

CONCURRENCY = 4  # respect ~100 RPM / 100K TPM; extractor also retries on 429


async def _extract_one(path: str, idx: int, sem: asyncio.Semaphore):
    async with sem:
        try:
            pages = await asyncio.to_thread(images.load_page_images, path)
            uri = await asyncio.to_thread(images.image_to_data_uri, pages[0])
            res = await asyncio.to_thread(extract.extract_page, uri)
            return path, idx, res, None
        except Exception as e:  # isolate: one bad doc must not kill the batch
            return path, idx, None, str(e)


async def run_job(paths: list[str]):
    """Async generator of SSE events: start -> doc* -> ledger -> done.

    Docs are extracted concurrently and streamed as they complete, so the UI
    fills fast. Reconciliation runs once over all extracted lines at the end.
    """
    t0 = time.perf_counter()
    yield {"type": "start", "total": len(paths)}

    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [asyncio.create_task(_extract_one(p, i, sem)) for i, p in enumerate(paths)]
    all_items = []
    errors = 0
    for fut in asyncio.as_completed(tasks):
        path, idx, res, err = await fut
        doc_id = path.replace("\\", "/").split("/")[-1]
        if err is not None:
            errors += 1
            yield {"type": "doc", "doc_id": doc_id, "idx": idx,
                   "line_items": [], "wall_ms": 0, "time_info": {}, "error": err}
            continue
        all_items.extend(res.line_items)
        yield {
            "type": "doc",
            "doc_id": doc_id,
            "idx": idx,
            "line_items": [li.model_dump(mode="json") for li in res.line_items],
            "wall_ms": round(res.wall_ms, 1),
            "time_info": res.time_info,
        }

    rr = reconcile.reconcile(all_items)
    yield {
        "type": "ledger",
        "ledger": [asdict(e) for e in rr.ledger],
        "recoups": [asdict(r) for r in rr.recoups],
        "totals": rr.totals,
        "needs_review": [li.model_dump(mode="json") for li in rr.needs_review],
    }
    yield {"type": "done", "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
           "errors": errors}
