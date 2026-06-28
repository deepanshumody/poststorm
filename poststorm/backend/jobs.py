import asyncio
import time
from dataclasses import asdict

from backend import baseline, extract, images, reconcile

CONCURRENCY = 4       # Cerebras lane
GEM_CONCURRENCY = 4   # Gemini lane (same, for a fair race)


def _stem(path: str) -> str:
    return path.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]


async def run_job(paths: list[str]):
    """Dual-provider race. Both lanes process the same pre-rendered images.
    Cerebras drives the posting grid / ledger / climax; Gemini is the GPU baseline.
    When Cerebras clears the batch we stop the (slower) Gemini lane and report how
    far it got — an honest throughput comparison.
    """
    total = len(paths)
    yield {"type": "start", "total": total}

    # Pre-decode identical inputs for both lanes (fair comparison, no double decode).
    uris = await asyncio.to_thread(
        lambda: [images.image_to_data_uri(images.load_page_images(p)[0], max_dim=1600) for p in paths]
    )
    t0 = time.perf_counter()

    cer_sem = asyncio.Semaphore(CONCURRENCY)
    gem_sem = asyncio.Semaphore(GEM_CONCURRENCY)

    async def cer_one(idx):
        async with cer_sem:
            try:
                res = await asyncio.to_thread(extract.extract_page, uris[idx])
                return ("cer", idx, res, None)
            except Exception as e:
                return ("cer", idx, None, str(e))

    async def gem_one(idx):
        async with gem_sem:
            r = await asyncio.to_thread(baseline.extract_page_gemini, uris[idx])
            return ("gem", idx, r)

    cer_tasks = {asyncio.create_task(cer_one(i)) for i in range(total)}
    gem_tasks = {asyncio.create_task(gem_one(i)) for i in range(total)}
    pending = cer_tasks | gem_tasks

    all_items, cer_left, errors = [], total, 0
    gem_done_n, gem_ok, gem_sum_ms = 0, 0, 0.0

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for d in done:
            r = d.result()
            if r[0] == "cer":
                _, idx, res, err = r
                doc_id = _stem(paths[idx])
                if err is not None:
                    errors += 1
                    yield {"type": "doc", "doc_id": doc_id, "idx": idx,
                           "line_items": [], "wall_ms": 0, "time_info": {}, "error": err}
                else:
                    all_items.extend(res.line_items)
                    yield {"type": "doc", "doc_id": doc_id, "idx": idx,
                           "line_items": [li.model_dump(mode="json") for li in res.line_items],
                           "wall_ms": round(res.wall_ms, 1), "time_info": res.time_info,
                           "usage": res.usage}
                cer_left -= 1
                if cer_left == 0:
                    cer_ms = (time.perf_counter() - t0) * 1000
                    rr = reconcile.reconcile(all_items)
                    yield {"type": "ledger",
                           "ledger": [asdict(e) for e in rr.ledger],
                           "recoups": [asdict(x) for x in rr.recoups],
                           "totals": rr.totals,
                           "needs_review": [li.model_dump(mode="json") for li in rr.needs_review]}
                    yield {"type": "cer_done", "elapsed_ms": round(cer_ms, 1), "count": total}
                    # Stop the slower Gemini lane and report progress.
                    for g in pending:
                        g.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    avg = round(gem_sum_ms / gem_ok, 1) if gem_ok else None
                    yield {"type": "gem_done", "completed": gem_ok, "attempted": gem_done_n,
                           "avg_ms": avg, "total": total}
                    yield {"type": "done", "elapsed_ms": round(cer_ms, 1), "errors": errors}
                    return
            else:
                _, idx, gr = r
                gem_done_n += 1
                if gr.get("ok"):
                    gem_ok += 1
                    gem_sum_ms += gr["elapsed_ms"]
                yield {"type": "gem", "doc_id": _stem(paths[idx]), "idx": idx,
                       "elapsed_ms": round(gr.get("elapsed_ms", 0), 1), "ok": gr.get("ok", False)}
