# PostStorm — Lockbox-to-Ledger

**Scanned paper EOBs → a reconciled, recoupment-aware ledger, faster than a human opens the first envelope.**

PostStorm reads the scanned "lockbox" remittance documents that pile up in a healthcare billing office, extracts every
line with **Gemma‑4 31B (multimodal) on Cerebras**, and runs a **deterministic** engine that reconciles them and catches
**cross‑patient recoupments** — where a payer silently claws back money on one patient by offsetting another's payment
inside the same check (a "dump account"). It posts a **who‑owes‑who ledger** and races a GPU baseline (Google Gemini) to
show how much faster Cerebras clears the batch.


> **Synthetic data only — no PHI.** All remittances are generated (see `generator/`). Nothing in this repo is real
> patient data.

---

## Results

A representative live run over the 24-document synthetic corpus (4 payer templates), Gemma‑4 31B on Cerebras
(`python -m eval.evaluate`):

| Metric | Result |
|---|---|
| **Recoupment recall** | **100%** (3/3 cross-patient dump accounts caught) |
| **Recoupment precision** | **100%** (0 false positives) |
| Paid-amount accuracy | 92.4% |
| Recoup-flag accuracy | 92.4% |
| Patient-name accuracy | 84.8% |
| Check-number accuracy (exact) | 77.3% |
| Lines extracted / expected | 63 / 66 |

The headline is the product metric — **100% recall / 100% precision on recoupment detection** — and it holds *despite*
imperfect per-field extraction. That's the architecture working as designed: the LLM's slips land mostly in free-form
IDs and the EFT-prefixed check strings, while the **deterministic** reconcile layer keys the dump-account match on
*amount + check + patient together* (and check numbers are page-consistent even when not byte-exact). The
catastrophic-exception-prone money math never runs in the model. (Extraction is non-deterministic, so exact numbers vary
run to run.)

---

## Quickstart (local)

```bash
cd poststorm
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example ../.env          # then add your CEREBRAS_API_KEY (and optional GEMINI_API_KEY)
python generator/generate.py     # generate the synthetic EOB fixtures (one-time)

uvicorn backend.main:app --port 8000
# open http://localhost:8000 and click "Read EOB batch"
```

## Run with Docker

```bash
cd poststorm
cp .env.example ../.env          # add your keys
docker compose up --build
# http://localhost:8000
```

The image is self-contained: it regenerates the fixtures at build time (deterministic + validated), runs as a non-root
user, and exposes a `/health` healthcheck. Secrets are injected as env vars — never baked into the image.

## Configuration (12-factor; all via env)

| Var | Default | Purpose |
|---|---|---|
| `CEREBRAS_API_KEY` | — | Cerebras key (Gemma‑4 31B). Required. |
| `CEREBRAS_MODEL` | `gemma-4-31b` | Model id. |
| `GEMINI_API_KEY` | — | Optional GPU baseline for the speed race. |
| `CORS_ORIGINS` | `http://localhost:8000` | Comma-separated allowlist. |
| `LOG_LEVEL` | `INFO` | Logging level. |
| `MAX_BATCH` | `48` | Hard cap on documents per job. |

## Develop

```bash
python -m pytest        # unit + API + orchestration tests (mocked; no live API calls)
ruff check .            # lint + import order
python generator/generate.py   # regenerate the 4-template synthetic EOB corpus
```

## System of record

Every completed batch is durably posted to an **event-sourced double-entry ledger** (`backend/ledger/`).  
Two read endpoints expose its state:

| Endpoint | Returns |
|---|---|
| `GET /ledger/balances` | Running balance per account (provider cash, claims, dump account) as integer cents. |
| `GET /ledger/audit` | Append-only event log — each event with its type, batch id, model + confidence provenance, `source_span`, and its balanced debit/credit entries. |

Storage defaults to **SQLite** (`data/ledger.db`; persisted in Docker via the `ledger-data` named volume).  
Override with **Postgres** by setting `DATABASE_URL=postgresql+psycopg2://...` in your `.env`.

### Human-in-the-loop review

Payments the model marks `confidence="low"` and ambiguous recoupments (a takeback whose amount matches more than
one payment, so the deterministic engine cannot pick) are routed to a **review queue** instead of being auto-posted.

**Two exception kinds:**
- `low_confidence` — extracted line flagged by the model; held until a reviewer confirms or corrects it.
- `ambiguous` — recoup that matches multiple same-amount payments; reviewer picks which one it offsets.

**Four resolve actions** (`POST /review/{id}/resolve`):
- `approve` — post the line as-is.
- `pick` — choose which candidate payment the ambiguous recoup offsets (requires `chosen_claim`).
- `correct` — edit a field (e.g. paid amount) and post the corrected line; the original vs. corrected pair is
  stored as a `Feedback` row — the seam for future improvement (captured, not yet consumed for retraining).
- `dismiss` — drop the line; nothing is posted.

Resolved lines are posted via `service.post_reviewed_line`, which writes an append-only ledger event tagged with the
`reviewer` string. The `ReviewException` row is a **mutable work-item** (status `open → resolved / dismissed`);
the ledger events it creates are immutable. Resolving an already-resolved exception is an idempotent no-op.

The dashboard includes a **Review queue** panel that surfaces all open exceptions inline — approve, pick, correct, or dismiss without leaving the page; resolving an item refreshes the System-of-record strip automatically.

**Endpoints:**

| Endpoint | Returns |
|---|---|
| `GET /review/queue?status=open` | Open (or filtered) exceptions with line, kind, and candidate claim ids (non-empty only for `ambiguous` exceptions; `low_confidence` exceptions carry an empty candidates list). |
| `POST /review/{id}/resolve` | Resolve one exception; body `{action, corrected?, chosen_claim?}`; invalid input → 400. |
| `GET /review/feedback` | All correction pairs (original vs. corrected) recorded so far. |

Reviewer identity is taken from the JWT `sub` claim (the issuing key's `kid`) and recorded in the ledger event. A `reviewer`-role JWT is required to call `POST /review/{id}/resolve` — see **Security & multi-tenancy** below.

## Security & multi-tenancy

PostStorm implements a full tenant-aware auth layer so multiple billing offices can share one deployment without seeing each other's data.

### Identity model

Every tenant is issued one or more **API keys** with the format `pk_<tenant>_<random>`.  
The raw key is shown exactly once at issuance and is never stored in plaintext — the server persists only `sha256(per_key_salt + raw_key)` alongside the salt.  
To obtain a session token, POST the raw key:

```
POST /auth/token   {"api_key": "pk_acme_..."}
→ {"access_token": "<jwt>", "token_type": "bearer", "expires_in": 1800}
```

The returned token is a **short-lived HS256 JWT** (`{sub: kid, tenant, role, iat, exp}`; default TTL 1800 s).  
Send it as `Authorization: Bearer <jwt>` on every data request.  
Missing, invalid, or expired tokens → **401** (with `WWW-Authenticate: Bearer` so clients can detect the scheme); insufficient role → **403**.

### Roles

Three roles in ascending order: `viewer < reviewer < admin`.

| Role | Can do |
|---|---|
| `viewer` | `GET /ledger/balances`, `GET /ledger/audit`, `GET /review/queue`, `GET /review/feedback` |
| `reviewer` | viewer + `POST /jobs`, `POST /review/{id}/resolve` |
| `admin` | reviewer + `GET /admin/audit`, `POST /admin/tenants`, `POST /admin/tenants/{id}/keys`, `DELETE /admin/keys/{kid}` |

### Tenant isolation

Every data-layer query is scoped to `principal.tenant`.  
A request for a resource owned by a different tenant returns **404** — the server does not reveal whether the resource exists for another tenant.

### Rate limiting

Requests are subject to a **per-tenant token-bucket** limiter (default: burst capacity 60, refill 5 req/s).  
Exhausting the bucket returns **429** with a `Retry-After` header (seconds until the next token is available).  
Override defaults with `RATE_BURST` and `RATE_RPS`.

### Audit log

Every mutating request (`POST`, `PUT`, `PATCH`, `DELETE`) against `/jobs`, `/review/*`, `/admin/*`, and `/auth/token` (token issuance) is recorded in an **append-only** `AuditLog` table with: `action`, `resource`, `principal` (key id), `tenant`, `status_code`, and `created_at`.  
`GET /admin/audit` (admin role) returns the log scoped to the caller's tenant.

### Admin operations

```
POST   /admin/tenants                     # create tenant + issue an initial key
POST   /admin/tenants/{tenant_id}/keys    # rotate / issue additional keys
DELETE /admin/keys/{kid}                  # revoke a key immediately
```

All three are admin-only. The two `POST` endpoints return the new `api_key` in the response (shown once — never retrievable again); `DELETE` returns a revocation confirmation, not a key.

### Demo & development

`GET /auth/demo-token` (enabled when `DEMO_MODE=true`, the default) issues a reviewer-role JWT for the `demo` tenant without requiring an API key — intended for the dashboard and local development only.

On startup, `SEED_TENANTS` (comma-separated `<tenant>:<role>` pairs, default `demo:reviewer`) creates tenant rows.  
Only the `demo` entry also seeds a deterministic API key (derived from `JWT_SECRET`); all other entries create the tenant row only — an admin must issue keys for them.

### Environment knobs

| Var | Default | Purpose |
|---|---|---|
| `JWT_SECRET` | `dev-insecure-change-me` | HS256 signing secret. The server logs a warning when the dev default is detected at startup. Set a real secret in production. |
| `JWT_TTL_SECONDS` | `1800` | JWT lifetime in seconds. |
| `DEMO_MODE` | `true` | Enables `GET /auth/demo-token`. Disable in production. |
| `SEED_TENANTS` | `demo:reviewer` | Comma-separated `<tenant>:<role>` pairs seeded on startup. |
| `RATE_BURST` | `60` | Token-bucket capacity per tenant. |
| `RATE_RPS` | `5.0` | Token-bucket refill rate (requests/second). |
| `ADMIN_BOOTSTRAP_KEY` | _(empty)_ | Raw admin key available for production bootstrap. |

### Extension points (not built)

The following are intentionally out of scope for this demo build:

- **External OIDC / IdP federation** — the `/auth/token` client-credentials flow is a drop-in seam; a future `POST /auth/oidc/token` could validate a third-party ID token and issue the same JWT.
- **KMS-backed key storage** — the current `sha256(salt + key)` hash is sufficient for demo scale; production deployments with stricter requirements would store key material in AWS KMS / GCP Cloud KMS.
- **Redis-backed rate limiting** — the in-memory token bucket is single-node; swap `ratelimit.RateLimiter` for a Redis-backed implementation to enforce limits across replicas.

---

## How it works

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the data flow, module boundaries, the recoupment-detection logic, and the
production posture (security, scalability limits, deployability).

Short version: a thin FastAPI **I/O shell** streams a dual-provider race over SSE; the **pure** `reconcile.py` core does
all the ledger math (the LLM only extracts), so the catastrophic-exception-prone matching is deterministic and unit-tested
— and every extracted line carries a `source_span` citation for auditability.
