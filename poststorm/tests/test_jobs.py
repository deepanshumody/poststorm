import asyncio

from backend import baseline, extract, images, jobs
from backend.extract import ExtractionResult
from backend.schema import LineItem


def _li(**kw):
    base = dict(
        claim_id="C1", payer="Aetna", patient_ref="A", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type="payment", recoup_flag=False, offset_link=None,
        check_number="CHK1", confidence="high", source_span="x",
    )
    base.update(kw)
    return LineItem(**base)


def _run(paths, monkeypatch, fail=False):
    def fake_extract(uri, **kw):
        if fail:
            raise RuntimeError("boom internal://secret-host/path")
        return ExtractionResult([_li()], {"total_time": 0.1, "completion_time": 0.05},
                                {"completion_tokens": 10}, {}, 100.0)

    monkeypatch.setattr(extract, "extract_page", fake_extract)
    monkeypatch.setattr(baseline, "extract_page_gemini",
                        lambda uri, **kw: {"ok": True, "elapsed_ms": 200.0, "status": "ok"})
    monkeypatch.setattr(images, "load_page_images", lambda p: ["img"])
    monkeypatch.setattr(images, "image_to_data_uri", lambda img, **kw: "data:image/png;base64,AA==")

    async def go():
        return [ev async for ev in jobs.run_job(paths)]

    return asyncio.run(go())


def test_run_job_event_sequence(monkeypatch):
    evs = _run(["a.png", "b.png"], monkeypatch)
    types = [e["type"] for e in evs]
    assert types[0] == "start"
    assert types[-1] == "done"
    for t in ("doc", "ledger", "cer_done", "gem_done"):
        assert t in types
    ledger = next(e for e in evs if e["type"] == "ledger")
    assert "totals" in ledger and ledger["totals"]["lines"] == 2


def test_extraction_error_is_generic_not_raw(monkeypatch):
    evs = _run(["a.png"], monkeypatch, fail=True)
    blob = str(evs)
    assert "secret-host" not in blob  # raw exception details never reach the client
    doc = next(e for e in evs if e["type"] == "doc")
    assert doc["error"] == "extraction_failed"
