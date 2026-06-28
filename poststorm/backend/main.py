import glob
import json
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.jobs import run_job

ROOT = Path(__file__).resolve().parents[1]
EOBS = ROOT / "data" / "eobs"
GT = ROOT / "data" / "ground_truth.json"
INDEX = ROOT / "frontend" / "index.html"

app = FastAPI(title="PostStorm")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/eobs", StaticFiles(directory=str(EOBS)), name="eobs")

JOBS: dict[str, list[str]] = {}


def _doc_meta() -> dict:
    truth = json.loads(GT.read_text())
    return {d["doc_id"]: d for d in truth}


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.post("/jobs")
def create_job(body: dict | None = None):
    body = body or {}
    n = int(body.get("count", 24))
    paths = [p for p in sorted(glob.glob(str(EOBS / "*.png"))) if ".thumb." not in Path(p).name][:n]
    meta = _doc_meta()
    docs = []
    for i, p in enumerate(paths):
        doc_id = Path(p).stem
        m = meta.get(doc_id, {})
        docs.append({
            "idx": i, "doc_id": doc_id,
            "img": f"/eobs/{doc_id}.png", "thumb": f"/eobs/{doc_id}.thumb.png",
            "has_recoup": m.get("has_planted_recoup", False),
            "recoup_box": m.get("recoup_box"),
            "recoup_text": m.get("recoup_text"),
            "payer": m.get("payer"),
        })
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = paths
    return {"job_id": jid, "count": len(paths), "docs": docs}


@app.get("/jobs/{jid}/stream")
async def stream(jid: str):
    paths = JOBS.get(jid, [])

    async def gen():
        async for ev in run_job(paths):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
def health():
    full = [p for p in glob.glob(str(EOBS / "*.png")) if ".thumb." not in Path(p).name]
    return {"ok": True, "docs": len(full)}
