import glob
import json
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from backend.jobs import run_job

app = FastAPI(title="PostStorm")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_INDEX = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
JOBS: dict[str, list[str]] = {}


@app.get("/")
def index():
    return FileResponse(_INDEX)


@app.post("/jobs")
def create_job(body: dict | None = None):
    body = body or {}
    n = int(body.get("count", 24))
    paths = sorted(glob.glob("data/eobs/*.png"))[:n]
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = paths
    return {"job_id": jid, "count": len(paths)}


@app.get("/jobs/{jid}/stream")
async def stream(jid: str):
    paths = JOBS.get(jid, [])

    async def gen():
        async for ev in run_job(paths):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
def health():
    return {"ok": True, "docs": len(glob.glob("data/eobs/*.png"))}
