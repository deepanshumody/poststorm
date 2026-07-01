import asyncio
import glob
import hashlib
import hmac
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import auth, ratelimit
from backend import metrics as metrics_module
from backend.config import get_settings
from backend.eval import groundtruth as eval_gt
from backend.eval import run as eval_run
from backend.ingest import queue as ingest_queue
from backend.ingest import storage as ingest_storage
from backend.ingest import worker as ingest_worker
from backend.ingest.models import Document, IngestJob
from backend.jobs import run_job
from backend.ledger import db as ledger_db
from backend.ledger import review as ledger_review
from backend.ledger import service as ledger_service
from backend.ledger.models import AuditLog, _now
from backend.logging_config import get_logger
from backend.writeback import worker as wb_worker
from backend.writeback.models import Delivery

ROOT = Path(__file__).resolve().parents[1]
EOBS = ROOT / "data" / "eobs"
GT = ROOT / "data" / "ground_truth.json"
INDEX = ROOT / "frontend" / "index.html"

log = get_logger("poststorm.api")
settings = get_settings()


def config_module_dev_secret() -> str:
    from backend.config import DEV_JWT_SECRET
    return DEV_JWT_SECRET


VERSION = "0.1.0"
MAX_JOBS = 64  # bounded in-memory store (single-node demo; no external DB by design)

_worker_stop: asyncio.Event | None = None
_worker_tasks: list = []


@asynccontextmanager
async def lifespan(app):
    global _worker_stop, _worker_tasks
    _s = ledger_db.SessionLocal()
    try:
        ingest_worker.recover_orphans(_s)
        ingest_queue.finalize_stranded_jobs(_s)
        wb_worker.recover_orphans(_s)
    except Exception:
        log.warning("startup recovery failed", exc_info=True)
    finally:
        _s.close()
    _worker_stop = asyncio.Event()
    _worker_tasks = [asyncio.create_task(ingest_worker.worker_loop(_worker_stop))
                     for _ in range(settings.ingest_workers)]
    _worker_tasks += [asyncio.create_task(wb_worker.worker_loop(_worker_stop))
                      for _ in range(settings.writeback_workers)]
    _worker_tasks.append(asyncio.create_task(wb_worker.relay_loop(_worker_stop)))
    try:
        yield
    finally:
        _worker_stop.set()
        await asyncio.gather(*_worker_tasks, return_exceptions=True)


app = FastAPI(title="PostStorm", version=VERSION, lifespan=lifespan)

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
app.mount("/eobs", StaticFiles(directory=str(EOBS)), name="eobs")

try:
    ledger_db.init_db()
except Exception:
    log.warning("ledger init_db failed; ledger persistence disabled this run", exc_info=True)

try:
    _seed_session = ledger_db.SessionLocal()
    try:
        auth.seed_tenants(_seed_session, settings)
    finally:
        _seed_session.close()
    if settings.jwt_secret == config_module_dev_secret():
        log.warning("JWT_SECRET is the insecure dev default — set a real secret in production")
except Exception:
    log.warning("tenant seeding failed; /auth/token may not work this run", exc_info=True)


JOBS: dict[str, tuple[str, list[str]]] = {}
STREAM_TICKETS: dict[str, tuple[str, str]] = {}  # ticket -> (jid, tenant); single-use
MAX_TICKETS = 256
WB_SINK: list = []      # dev-only: received webhook postings (bounded)
WB_SINK_MAX = 256


def _new_stream_ticket(job_id: str, tenant: str) -> str:
    ticket = secrets.token_urlsafe(16)
    if len(STREAM_TICKETS) >= MAX_TICKETS:
        STREAM_TICKETS.pop(next(iter(STREAM_TICKETS)))
    STREAM_TICKETS[ticket] = (job_id, tenant)
    return ticket


_AUDIT_ACTIONS = {
    ("POST", "/auth/token"): "auth.token",
    ("POST", "/jobs"): "job.create",
    ("POST", "/admin/tenants"): "tenant.create",
    ("POST", "/documents"): "document.upload",
    ("POST", "/documents/demo-batch"): "ingest.demo_batch",
    ("POST", "/eval/run"): "eval.run",
}


def _audit_action(method: str, path: str) -> str:
    if (method, path) in _AUDIT_ACTIONS:
        return _AUDIT_ACTIONS[(method, path)]
    if method == "POST" and path.startswith("/ingest/documents/") and path.endswith("/retry"):
        return "document.retry"
    if method == "POST" and path.startswith("/writeback/deliveries/") and path.endswith("/retry"):
        return "writeback.retry"
    if method == "POST" and path.startswith("/review/") and path.endswith("/resolve"):
        return "review.resolve"
    if method == "POST" and path.startswith("/admin/tenants/") and path.endswith("/keys"):
        return "key.issue"
    if method == "DELETE" and path.startswith("/admin/keys/"):
        return "key.revoke"
    return f"{method} {path}"


_AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class JobRequest(BaseModel):
    count: int = Field(default=24, ge=1, le=48)


class ResolveRequest(BaseModel):
    action: str
    corrected: dict | None = None
    chosen_claim: str | None = None


class TokenRequest(BaseModel):
    api_key: str


class TenantRequest(BaseModel):
    tenant_id: str
    name: str | None = None
    role: str = "reviewer"


class KeyRequest(BaseModel):
    role: str = "reviewer"


@app.post("/auth/token")
def auth_token(req: TokenRequest, response: Response, _a: None = Depends(ratelimit.enforce_auth)):
    s = ledger_db.SessionLocal()
    try:
        principal = auth.verify_api_key(s, req.api_key)
    finally:
        s.close()
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid api key",
                            headers={"WWW-Authenticate": "Bearer"})
    token = auth.issue_jwt(principal, settings.jwt_secret, settings.jwt_ttl_seconds)
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.jwt_ttl_seconds}


@app.get("/auth/demo-token")
def auth_demo_token(response: Response):
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="not found")
    principal = auth.Principal(tenant="demo", role="reviewer", sub="demo-key")
    token = auth.issue_jwt(principal, settings.jwt_secret, settings.jwt_ttl_seconds)
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.jwt_ttl_seconds}


@app.get("/auth/whoami")
def auth_whoami(principal: auth.Principal = Depends(auth.require_principal)):
    return {"tenant": principal.tenant, "role": principal.role, "sub": principal.sub}


@app.get("/ledger/balances")
def ledger_balances(principal: auth.Principal = Depends(auth.require_role("viewer")),
                    _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        return ledger_service.balances(s, principal.tenant)
    finally:
        s.close()


@app.get("/ledger/audit")
def ledger_audit(limit: int = 50, principal: auth.Principal = Depends(auth.require_role("viewer")),
                 _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        return {"events": ledger_service.audit_trail(s, principal.tenant, max(0, min(limit, 200)))}
    finally:
        s.close()


@app.get("/review/queue")
def review_queue(status: str = "open",
                 principal: auth.Principal = Depends(auth.require_role("viewer")),
                 _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        return {"items": ledger_review.review_queue(s, principal.tenant, status)}
    finally:
        s.close()


@app.post("/review/{exc_id}/resolve")
def review_resolve(exc_id: int, req: ResolveRequest,
                   principal: auth.Principal = Depends(auth.require_role("reviewer")),
                   _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        return ledger_review.resolve(s, principal.tenant, exc_id, req.action,
                                     req.corrected, req.chosen_claim, reviewer=principal.sub)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        s.close()


@app.get("/review/feedback")
def review_feedback(principal: auth.Principal = Depends(auth.require_role("viewer")),
                    _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        return {"feedback": ledger_review.feedback_list(s, principal.tenant)}
    finally:
        s.close()


@app.get("/ingest/jobs/{job_id}")
def ingest_job_status(job_id: str,
                      principal: auth.Principal = Depends(auth.require_role("viewer")),
                      _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        job = s.get(IngestJob, job_id)
        if job is None or job.tenant_id != principal.tenant:
            raise HTTPException(status_code=404, detail="job not found")
        docs = s.query(Document).filter_by(job_id=job_id).all()
        return {"job_id": job.id, "status": job.status, "doc_count": job.doc_count,
                "post_summary": json.loads(job.post_summary) if job.post_summary else None,
                "documents": [{"id": d.id, "filename": d.filename, "status": d.status,
                               "attempts": d.attempts, "error": d.error} for d in docs]}
    finally:
        s.close()


@app.post("/documents")
async def upload_documents(files: list[UploadFile] = File(...),
                           principal: auth.Principal = Depends(auth.require_role("reviewer")),
                           _rl: auth.Principal = Depends(ratelimit.enforce)):
    if not files:
        raise HTTPException(status_code=422, detail="no files provided")
    s = ledger_db.SessionLocal()
    try:
        specs = []
        for f in files:
            data = await f.read()
            try:
                blob = ingest_storage.save_upload(principal.tenant, f.filename or "upload", data)
            except ingest_storage.UploadError as e:
                raise HTTPException(status_code=e.status_code, detail=str(e)) from e
            specs.append(ingest_queue.DocSpec(
                doc_id=blob.doc_id, filename=ingest_storage._safe_name(f.filename or "upload"),
                content_type=blob.content_type, storage_path=blob.storage_path))
        job_id = ingest_queue.enqueue_job(s, principal.tenant, specs)
        docs = s.query(Document).filter_by(job_id=job_id).all()
        return {"job_id": job_id,
                "documents": [{"id": d.id, "filename": d.filename, "status": d.status} for d in docs],
                "stream_ticket": _new_stream_ticket(job_id, principal.tenant)}
    finally:
        s.close()


@app.post("/documents/demo-batch")
def documents_demo_batch(count: int = 6,
                         principal: auth.Principal = Depends(auth.require_role("reviewer")),
                         _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        paths = ingest_storage.fixture_paths(max(1, min(count, settings.max_batch)))
        specs = [ingest_queue.DocSpec(doc_id="d_" + uuid.uuid4().hex[:12], filename=Path(p).name,
                                      content_type="image/png", storage_path=p) for p in paths]
        job_id = ingest_queue.enqueue_job(s, principal.tenant, specs)
        docs = s.query(Document).filter_by(job_id=job_id).all()
        return {"job_id": job_id,
                "documents": [{"id": d.id, "filename": d.filename, "status": d.status} for d in docs],
                "stream_ticket": _new_stream_ticket(job_id, principal.tenant)}
    finally:
        s.close()


@app.post("/ingest/documents/{doc_id}/retry")
def ingest_retry_document(doc_id: str,
                          principal: auth.Principal = Depends(auth.require_role("reviewer")),
                          _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        doc = s.get(Document, doc_id)
        if doc is None or doc.tenant_id != principal.tenant:
            raise HTTPException(status_code=404, detail="document not found")
        if doc.status == "failed":
            doc.status = "pending"
            doc.error = None
            doc.attempts = 0
            doc.updated_at = _now()
            job = s.get(IngestJob, doc.job_id)
            if job is not None and job.status in ("finalized", "partially_failed"):
                job.status = "processing"
                job.finalized_at = None
            s.commit()
        return {"id": doc.id, "status": doc.status}
    finally:
        s.close()


@app.post("/writeback/mock-sink")
async def writeback_mock_sink(request: Request):
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="not found")
    body = await request.body()
    expected = "sha256=" + hmac.new(settings.writeback_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-Signature", ""), expected):
        raise HTTPException(status_code=401, detail="bad signature")
    key = request.headers.get("Idempotency-Key", "")
    if any(x["idempotency_key"] == key for x in WB_SINK):
        return JSONResponse({"status": "duplicate"}, status_code=409)
    if len(WB_SINK) >= WB_SINK_MAX:
        WB_SINK.pop(0)
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    WB_SINK.append({"idempotency_key": key, "body": parsed})
    return {"status": "received"}


@app.get("/writeback/mock-sink")
def writeback_mock_sink_list(principal: auth.Principal = Depends(auth.require_role("viewer")),
                             _rl: auth.Principal = Depends(ratelimit.enforce)):
    # Dev-only, demo_mode-gated: a GLOBAL stand-in for a single external receiver — it returns
    # ALL received postings (not tenant-scoped). Not for shared multi-tenant prod.
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="not found")
    return {"received": WB_SINK[-50:]}


@app.get("/writeback/deliveries")
def writeback_deliveries(status: str | None = None,
                         principal: auth.Principal = Depends(auth.require_role("viewer")),
                         _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        q = s.query(Delivery).filter_by(tenant_id=principal.tenant)
        if status:
            q = q.filter_by(status=status)
        rows = q.order_by(Delivery.id.desc()).limit(200)
        return {"deliveries": [{"id": d.id, "event_id": d.event_id, "destination": d.destination,
                                "status": d.status, "attempts": d.attempts, "last_error": d.last_error,
                                "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None}
                               for d in rows]}
    finally:
        s.close()


@app.post("/writeback/deliveries/{delivery_id}/retry")
def writeback_retry(delivery_id: int,
                    principal: auth.Principal = Depends(auth.require_role("reviewer")),
                    _rl: auth.Principal = Depends(ratelimit.enforce)):
    s = ledger_db.SessionLocal()
    try:
        d = s.get(Delivery, delivery_id)
        if d is None or d.tenant_id != principal.tenant:
            raise HTTPException(status_code=404, detail="delivery not found")
        if d.status in ("dead", "failed"):
            d.status = "pending"
            d.attempts = 0
            d.last_error = None
            d.updated_at = _now()
            s.commit()
        return {"id": d.id, "status": d.status}
    finally:
        s.close()


@app.get("/eval/report")
def eval_report(principal: auth.Principal = Depends(auth.require_role("viewer")),
                _rl: auth.Principal = Depends(ratelimit.enforce)):
    report = eval_run.read_report()
    if report is None:
        raise HTTPException(status_code=404, detail="no eval report yet")
    return report


@app.post("/eval/run")
async def eval_run_endpoint(count: int = 0,
                            principal: auth.Principal = Depends(auth.require_role("reviewer")),
                            _rl: auth.Principal = Depends(ratelimit.enforce)):
    paths = _full_pngs()
    if count and count > 0:
        paths = paths[:count]
    report = await asyncio.to_thread(eval_run.run_eval, paths, eval_gt.load(), settings.cerebras_model)
    await asyncio.to_thread(eval_run.write_report, report)
    return report


@app.get("/ingest/jobs/{job_id}/stream")
async def ingest_job_stream(job_id: str, ticket: str = ""):
    bound = STREAM_TICKETS.pop(ticket, None)  # single-use
    if bound is None or bound[0] != job_id:
        raise HTTPException(status_code=404, detail="job not found")
    tenant = bound[1]

    async def gen():
        last = None
        for _ in range(600):  # ~5 min ceiling at 0.5s/tick
            s = ledger_db.SessionLocal()
            try:
                job = s.get(IngestJob, job_id)
                if job is None or job.tenant_id != tenant:
                    return
                docs = s.query(Document).filter_by(job_id=job_id).all()
                snap = {"status": job.status,
                        "documents": [{"id": d.id, "status": d.status, "error": d.error} for d in docs]}
                payload = json.dumps(snap, sort_keys=True)
                if payload != last:
                    last = payload
                    yield f"data: {json.dumps({'type': 'status', **snap})}\n\n"
                if job.status in ("finalized", "partially_failed"):
                    yield ("data: " + json.dumps({"type": "finalized", "status": job.status,
                           "post_summary": json.loads(job.post_summary) if job.post_summary else None}) + "\n\n")
                    return
            finally:
                s.close()
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.middleware("http")
async def audit_log(request: Request, call_next):
    resp = await call_next(request)
    method = request.method
    path = request.url.path
    action = _audit_action(method, path)
    is_auth_token = (method == "POST" and path == "/auth/token")
    if method in _AUDITED_METHODS and (
        path.startswith(("/jobs", "/review/", "/admin/", "/documents", "/ingest/", "/writeback/", "/eval/"))
        or is_auth_token):
        principal = getattr(request.state, "principal", None)
        s = ledger_db.SessionLocal()
        try:
            s.add(AuditLog(
                tenant_id=(principal.tenant if principal else ""),
                principal=(principal.sub if principal else ""),
                action=action, resource=path, status_code=resp.status_code))
            s.commit()
        except Exception:
            log.warning("audit write failed", exc_info=True)
        finally:
            s.close()
    return resp


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


@app.get("/admin/audit")
def admin_audit(limit: int = 100,
                principal: auth.Principal = Depends(auth.require_role("admin"))):
    s = ledger_db.SessionLocal()
    try:
        rows = (s.query(AuditLog).filter_by(tenant_id=principal.tenant)
                .order_by(AuditLog.id.desc()).limit(max(0, min(limit, 500))))
        return {"events": [{"id": r.id, "action": r.action, "resource": r.resource,
                            "principal": r.principal, "status_code": r.status_code,
                            "created_at": r.created_at.isoformat()} for r in rows]}
    finally:
        s.close()


@app.post("/admin/tenants")
def admin_create_tenant(req: TenantRequest,
                        principal: auth.Principal = Depends(auth.require_role("admin"))):
    s = ledger_db.SessionLocal()
    try:
        auth.create_tenant(s, req.tenant_id, req.name or "")
        kid, raw = auth.issue_key(s, req.tenant_id, req.role)
        s.commit()
        return {"tenant_id": req.tenant_id, "kid": kid, "api_key": raw, "role": req.role}
    finally:
        s.close()


@app.post("/admin/tenants/{tenant_id}/keys")
def admin_issue_key(tenant_id: str, req: KeyRequest,
                    principal: auth.Principal = Depends(auth.require_role("admin"))):
    s = ledger_db.SessionLocal()
    try:
        auth.create_tenant(s, tenant_id)  # no-op if it already exists
        kid, raw = auth.issue_key(s, tenant_id, req.role)
        s.commit()
        return {"tenant_id": tenant_id, "kid": kid, "api_key": raw, "role": req.role}
    finally:
        s.close()


@app.delete("/admin/keys/{kid}")
def admin_revoke_key(kid: str,
                     principal: auth.Principal = Depends(auth.require_role("admin"))):
    s = ledger_db.SessionLocal()
    try:
        ok = auth.revoke_key(s, kid)
        if ok:
            s.commit()
    finally:
        s.close()
    if not ok:
        raise HTTPException(status_code=404, detail="key not found")
    return {"kid": kid, "revoked": True}


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.post("/jobs")
def create_job(req: JobRequest, principal: auth.Principal = Depends(auth.require_role("reviewer")),
               _rl: auth.Principal = Depends(ratelimit.enforce)):
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
    JOBS[jid] = (principal.tenant, paths)
    ticket = secrets.token_urlsafe(16)
    if len(STREAM_TICKETS) >= MAX_TICKETS:
        STREAM_TICKETS.pop(next(iter(STREAM_TICKETS)))
    STREAM_TICKETS[ticket] = (jid, principal.tenant)
    log.info("job created id=%s tenant=%s count=%d", jid, principal.tenant, len(paths))
    return {"job_id": jid, "count": len(paths), "docs": docs, "stream_ticket": ticket}


@app.get("/jobs/{jid}/stream")
async def stream(jid: str, ticket: str = ""):
    bound = STREAM_TICKETS.pop(ticket, None)  # single-use: consume on read
    if bound is None or bound[0] != jid or jid not in JOBS:
        raise HTTPException(status_code=404, detail="job not found")
    tenant, paths = JOBS[jid]
    if bound[1] != tenant:
        raise HTTPException(status_code=404, detail="job not found")

    async def gen():
        async for ev in run_job(tenant, paths):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
def health():
    return {"ok": True, "version": VERSION, "model": settings.cerebras_model, "docs": len(_full_pngs())}


@app.get("/metrics")
def metrics_endpoint():
    s = ledger_db.SessionLocal()
    try:
        return Response(metrics_module.render_metrics(s), media_type="text/plain; version=0.0.4")
    finally:
        s.close()
