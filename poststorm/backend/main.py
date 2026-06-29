import glob
import json
import secrets
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import auth
from backend.config import get_settings
from backend.jobs import run_job
from backend.ledger import db as ledger_db
from backend.ledger import review as ledger_review
from backend.ledger import service as ledger_service
from backend.ledger.models import AuditLog
from backend.logging_config import get_logger

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

app = FastAPI(title="PostStorm", version=VERSION)

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


_AUDIT_ACTIONS = {
    ("POST", "/auth/token"): "auth.token",
    ("POST", "/jobs"): "job.create",
    ("POST", "/admin/tenants"): "tenant.create",
}


def _audit_action(method: str, path: str) -> str:
    if (method, path) in _AUDIT_ACTIONS:
        return _AUDIT_ACTIONS[(method, path)]
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


@app.post("/auth/token")
def auth_token(req: TokenRequest):
    s = ledger_db.SessionLocal()
    try:
        principal = auth.verify_api_key(s, req.api_key)
    finally:
        s.close()
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid api key",
                            headers={"WWW-Authenticate": "Bearer"})
    token = auth.issue_jwt(principal, settings.jwt_secret, settings.jwt_ttl_seconds)
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.jwt_ttl_seconds}


@app.get("/auth/demo-token")
def auth_demo_token():
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="not found")
    principal = auth.Principal(tenant="demo", role="reviewer", sub="demo-key")
    token = auth.issue_jwt(principal, settings.jwt_secret, settings.jwt_ttl_seconds)
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.jwt_ttl_seconds}


@app.get("/auth/whoami")
def auth_whoami(principal: auth.Principal = Depends(auth.require_principal)):
    return {"tenant": principal.tenant, "role": principal.role, "sub": principal.sub}


@app.get("/ledger/balances")
def ledger_balances(principal: auth.Principal = Depends(auth.require_role("viewer"))):
    s = ledger_db.SessionLocal()
    try:
        return ledger_service.balances(s, principal.tenant)
    finally:
        s.close()


@app.get("/ledger/audit")
def ledger_audit(limit: int = 50, principal: auth.Principal = Depends(auth.require_role("viewer"))):
    s = ledger_db.SessionLocal()
    try:
        return {"events": ledger_service.audit_trail(s, principal.tenant, max(0, min(limit, 200)))}
    finally:
        s.close()


@app.get("/review/queue")
def review_queue(status: str = "open",
                 principal: auth.Principal = Depends(auth.require_role("viewer"))):
    s = ledger_db.SessionLocal()
    try:
        return {"items": ledger_review.review_queue(s, principal.tenant, status)}
    finally:
        s.close()


@app.post("/review/{exc_id}/resolve")
def review_resolve(exc_id: int, req: ResolveRequest,
                   principal: auth.Principal = Depends(auth.require_role("reviewer"))):
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
def review_feedback(principal: auth.Principal = Depends(auth.require_role("viewer"))):
    s = ledger_db.SessionLocal()
    try:
        return {"feedback": ledger_review.feedback_list(s, principal.tenant)}
    finally:
        s.close()


@app.middleware("http")
async def audit_log(request: Request, call_next):
    resp = await call_next(request)
    method = request.method
    path = request.url.path
    action = _audit_action(method, path)
    is_auth_token = (method == "POST" and path == "/auth/token")
    if method in _AUDITED_METHODS and (path.startswith(("/jobs", "/review/", "/admin/")) or is_auth_token):
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


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.post("/jobs")
def create_job(req: JobRequest, principal: auth.Principal = Depends(auth.require_role("reviewer"))):
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
