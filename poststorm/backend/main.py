import glob
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.jobs import run_job
from backend.ledger import db as ledger_db
from backend.ledger import review as ledger_review
from backend.ledger import service as ledger_service
from backend.logging_config import get_logger

ROOT = Path(__file__).resolve().parents[1]
EOBS = ROOT / "data" / "eobs"
GT = ROOT / "data" / "ground_truth.json"
INDEX = ROOT / "frontend" / "index.html"

log = get_logger("poststorm.api")
settings = get_settings()
VERSION = "0.1.0"
MAX_JOBS = 64  # bounded in-memory store (single-node demo; no external DB by design)

app = FastAPI(title="PostStorm", version=VERSION)

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/eobs", StaticFiles(directory=str(EOBS)), name="eobs")

try:
    ledger_db.init_db()
except Exception:
    log.warning("ledger init_db failed; ledger persistence disabled this run", exc_info=True)


JOBS: dict[str, list[str]] = {}


class JobRequest(BaseModel):
    count: int = Field(default=24, ge=1, le=48)


class ResolveRequest(BaseModel):
    action: str
    corrected: dict | None = None
    chosen_claim: str | None = None


@app.get("/ledger/balances")
def ledger_balances():
    s = ledger_db.SessionLocal()
    try:
        return ledger_service.balances(s, "demo")
    finally:
        s.close()


@app.get("/ledger/audit")
def ledger_audit(limit: int = 50):
    s = ledger_db.SessionLocal()
    try:
        return {"events": ledger_service.audit_trail(s, "demo", max(0, min(limit, 200)))}
    finally:
        s.close()


@app.get("/review/queue")
def review_queue(status: str = "open"):
    s = ledger_db.SessionLocal()
    try:
        return {"items": ledger_review.review_queue(s, "demo", status)}
    finally:
        s.close()


@app.post("/review/{exc_id}/resolve")
def review_resolve(exc_id: int, req: ResolveRequest):
    s = ledger_db.SessionLocal()
    try:
        return ledger_review.resolve(s, "demo", exc_id, req.action, req.corrected, req.chosen_claim)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        s.close()


@app.get("/review/feedback")
def review_feedback():
    s = ledger_db.SessionLocal()
    try:
        return {"feedback": ledger_review.feedback_list(s, "demo")}
    finally:
        s.close()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'"
    )
    return resp


def _doc_meta() -> dict:
    return {d["doc_id"]: d for d in json.loads(GT.read_text())}


def _full_pngs() -> list[str]:
    return [p for p in sorted(glob.glob(str(EOBS / "*.png"))) if ".thumb." not in Path(p).name]


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.post("/jobs")
def create_job(req: JobRequest):
    n = min(req.count, settings.max_batch)
    paths = _full_pngs()[:n]
    meta = _doc_meta()
    docs = []
    for i, p in enumerate(paths):
        doc_id = Path(p).stem
        m = meta.get(doc_id, {})
        docs.append({
            "idx": i, "doc_id": doc_id,
            "img": f"/eobs/{doc_id}.png", "thumb": f"/eobs/{doc_id}.thumb.png",
            "has_recoup": m.get("has_planted_recoup", False),
            "recoup_box": m.get("recoup_box"), "recoup_text": m.get("recoup_text"),
            "payer": m.get("payer"),
        })
    jid = uuid.uuid4().hex[:8]
    if len(JOBS) >= MAX_JOBS:
        JOBS.pop(next(iter(JOBS)))  # evict oldest
    JOBS[jid] = paths
    log.info("job created id=%s count=%d", jid, len(paths))
    return {"job_id": jid, "count": len(paths), "docs": docs}


@app.get("/jobs/{jid}/stream")
async def stream(jid: str):
    if jid not in JOBS:
        raise HTTPException(status_code=404, detail="job not found")
    paths = JOBS[jid]

    async def gen():
        async for ev in run_job(paths):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
def health():
    return {"ok": True, "version": VERSION, "model": settings.cerebras_model, "docs": len(_full_pngs())}
