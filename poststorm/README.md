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

## Durable ingest & extraction

The synchronous race demo (above) processes EOBs as part of a live-streamed job. The durable pipeline accepts real uploads or fixture batches, persists every step to SQLite, and survives server restarts. Both paths run alongside each other without interference.

### Intake routes

| Endpoint | Description |
|---|---|
| `POST /documents` | Multipart upload — one or more PDF / PNG / JPEG files. Reviewer role required. Validates content type (415) and file size (413) before writing. Returns `job_id` + `stream_ticket`. |
| `POST /documents/demo-batch?count=N` | Seeds N fixture EOBs (from `data/eobs/`) through the same durable pipeline without a file upload. Returns `job_id` + `stream_ticket`. |

### Pipeline

Intake persists one `IngestJob` + one `Document` row per file, all with status `pending`.
Lifespan-managed asyncio workers (started at server startup, stopped cleanly on shutdown) claim
documents atomically (`pending → processing`, predicate-guarded so no two workers claim the same doc),
then extract with Cerebras — reusing the same retry/backoff as the synchronous race.
Multi-page documents are supported: each page is extracted in sequence and the items are concatenated
into a single `Extraction` row.
Once every document in a job reaches a terminal state, `maybe_finalize_job` reconciles the extracted
lines from the successfully-extracted subset and posts them to the ledger (idempotent via
the ledger's `line_key` unique constraint).

### Partial-failure policy

A failed document does not block finalize. When all docs are terminal and at least one failed, the job
status becomes `partially_failed`; the successful subset is still posted. Recoups whose offset lies in
a failed document are held in the review queue until the doc is retried.
`POST /ingest/documents/{doc_id}/retry` (reviewer role) resets the doc to `pending` and reopens the
job to `processing` — workers will pick it up and attempt extraction again (up to `INGEST_MAX_ATTEMPTS`
total attempts across all retry calls).

### Status and streaming

| Endpoint | Description |
|---|---|
| `GET /ingest/jobs/{job_id}` | Viewer role. Returns per-document status, attempt counts, error strings, and `post_summary` once the job is finalized. |
| `GET /ingest/jobs/{job_id}/stream?ticket=<ticket>` | SSE stream of status snapshots. The single-use ticket is minted at intake time and consumed on first connection — the JWT never appears in a URL. |

### Restart-survivable

All job and document state lives in SQLite. On startup, `recover_orphans` resets any `Document` rows
left in `processing` (abandoned by a previous crash mid-flight) back to `pending`, so workers
re-try them automatically.

### Configuration knobs

| Var | Default | Purpose |
|---|---|---|
| `INGEST_WORKERS` | `4` | Number of in-process asyncio worker tasks started at lifespan. |
| `INGEST_MAX_ATTEMPTS` | `3` | Per-document attempt ceiling; exhausted → `failed`. |
| `INGEST_IDLE_SLEEP` | `0.25` | Worker poll interval (seconds) when the queue is empty. |
| `MAX_UPLOAD_MB` | `15` | Maximum upload file size for `POST /documents`. |
| `UPLOAD_DIR` | `./data/uploads` | Root directory for uploaded files; tenant-isolated subdirectories are created automatically. |

### Extension points (not built)

The following are intentionally out of scope for this demo build:

- **External broker (Redis / Celery)** — the in-process `worker_loop` is the natural swap point for a distributed task queue when multi-node scale is needed.
- **Multi-node workers** — the atomic predicate claim works correctly with multiple processes against PostgreSQL; SQLite's write lock limits the current deploy to a single node.
- **Encryption-at-rest / PHI handling** — uploaded files are stored as-written; this build uses synthetic data only. Production deployments handling real EOBs would add at-rest encryption and PHI controls before persisting uploads.

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

## Write-back / delivery

Once a batch is posted to the ledger, an **event-sourced relay** (`backend/writeback/`) projects a durable `Delivery`
outbox from the append-only ledger event log — one row per event per configured destination. Lifespan-managed asyncio
workers claim and push each delivery to the destination, completing the lockbox-to-ledger-to-downstream loop.

### Destinations

**File export** — writes two files to `<EXPORT_DIR>/<tenant>/<idempotency_key>`:

- `.json` — the canonical posting: all ledger entry fields, provenance (model, confidence, `source_line_key`), and the
  idempotency key.
- `.835.txt` — a **representative** ERA-style remittance (BPR / TRN / N1 / CLP / SVC / PLB segments). Clearly labeled
  in-file as *not* standards-valid X12; the full 835 EDI generator is a documented extension point.

**HMAC-signed webhook** — POSTs the canonical JSON to `WRITEBACK_WEBHOOK_URL` with:

- `X-Signature: sha256=<hmac-sha256>` — signed with `WRITEBACK_WEBHOOK_SECRET`.
- `Idempotency-Key: <idempotency_key>` — for receiver-side dedup.
- HTTP 409 is treated as success (receiver already saw the key); 5xx / timeout is retryable; other 4xx is permanent.

### Delivery semantics

- **At-least-once** with a **stable idempotency key** (`sha256(tenant|event_id|destination)`) — the deterministic file
  path on disk and the `Idempotency-Key` header on the wire make duplicate deliveries safe at the receiver.
- **Retry / backoff** — failed-but-retryable deliveries return to `pending`; after `WRITEBACK_MAX_ATTEMPTS` attempts the
  row moves to `dead` (dead-letter state).
- **Restart-survivable** — `recover_orphans` resets any `delivering` rows back to `pending` on startup, so in-flight
  deliveries from a previous crash are retried automatically.
- **No double-enqueue** — the `(tenant_id, event_id, destination)` unique constraint makes relay runs idempotent.

### Endpoints

| Endpoint | Role | Description |
|---|---|---|
| `GET /writeback/deliveries` | viewer | Outbox viewer — all delivery rows (optional `?status=` filter). |
| `POST /writeback/deliveries/{id}/retry` | reviewer | Reset a `failed` or `dead` delivery to `pending` (attempts reset to 0). |
| `POST /writeback/mock-sink` | — | Dev-only loopback: verifies `X-Signature`, deduplicates on `Idempotency-Key`, stores the payload. Gated by `DEMO_MODE=true`. |
| `GET /writeback/mock-sink` | viewer | List payloads received by the mock sink. |

The mock sink lets the demo show end-to-end signed delivery without a real downstream system. The mock sink is a global demo stand-in (not tenant-scoped) and is `demo_mode`-gated.

### Configuration knobs

| Var | Default | Purpose |
|---|---|---|
| `WRITEBACK_DESTINATIONS` | `file` | Comma-separated active destinations (`file`, `webhook`). |
| `WRITEBACK_WEBHOOK_URL` | _(empty)_ | Target URL for the webhook destination; empty skips webhook delivery. |
| `WRITEBACK_WEBHOOK_SECRET` | `dev-writeback-secret` | HMAC-SHA256 signing secret. Set a real secret in production. |
| `WRITEBACK_MAX_ATTEMPTS` | `5` | Attempt ceiling; exhausted → `dead`. |
| `WRITEBACK_WORKERS` | `2` | Lifespan delivery worker tasks. |
| `WRITEBACK_IDLE_SLEEP` | `0.25` | Worker poll interval (seconds) when the queue is empty. |
| `EXPORT_DIR` | `./data/exports` | Root directory for file exports; tenant-isolated subdirectories created automatically. |

### Extension points (not built)

The following are intentionally out of scope for this demo build:

- **Standards-valid X12 835 EDI** — `to_835` in `payload.py` produces a representative, human-readable ERA for demo
  purposes; a production 835 generator would use a certified X12 library and emit proper loop/segment-count envelopes.
- **Real downstream connectors** — Epic, athenahealth, SFTP, and clearinghouse APIs are the natural swap-points for
  the adapter functions; `deliver_file` / `deliver_webhook` is the extension seam.
- **External broker** — the in-process `worker_loop` / `relay_loop` are the natural swap-points for a distributed task
  queue (Redis / Celery) when multi-node scale is needed.

## Observability & eval

### Eval harness

The eval harness scores the pipeline against `data/ground_truth.json` using a **pure, deterministic scorer** — no network calls, no side effects.

Three scoring dimensions:

- **Extraction field-accuracy** — for every claim id that appears in both the extraction and the ground truth, each field (paid, charge, allowed, adjustment, claim\_id, payer, patient\_ref, carc, recoup\_flag, event\_type) is compared. Money fields are compared to the cent (`EVAL_MONEY_TOLERANCE_CENTS`; default 0 = exact). Reports overall accuracy and per-field breakdown.
- **Recoup precision / recall / F1** — precision is over the auto-resolved `matched` set (caught planted claims / all matched claims); recall counts both `matched` and `needs_review` recoup claims as detected (a claim the engine routes to review is still caught, not missed); F1 is the harmonic mean.
- **Confidence calibration** — error rates for `low`- vs `high`-confidence lines among matched claims (does the model's self-reported confidence predict actual errors?).

**Run it:**

```bash
# CLI — extracts the fixture corpus, writes EVAL_DIR/report.json
python -m backend.eval.run

# HTTP — reviewer role required
POST /eval/run          # runs and writes the report; returns the report JSON
GET  /eval/report       # viewer role — returns the most recent report JSON (404 if none)
```

### Prometheus metrics

`GET /metrics` is **open** (no auth, like `/health`) and returns aggregate-only operational gauges in Prometheus text format. No PHI, no per-tenant labels.

Exposed metrics:

| Metric | Description |
|---|---|
| `poststorm_ledger_events` | Total ledger events |
| `poststorm_dump_exposure_cents` | Parked dump-account balance (cents) |
| `poststorm_review_exceptions{status=...}` | Review exceptions by status |
| `poststorm_deliveries{status=...}` | Deliveries by status |
| `poststorm_ingest_jobs{status=...}` | Ingest jobs by status |
| `poststorm_documents{status=...}` | Documents by status |
| `poststorm_field_accuracy` | Latest eval: overall field accuracy |
| `poststorm_recoup_precision` | Latest eval: recoup precision |
| `poststorm_recoup_recall` | Latest eval: recoup recall |
| `poststorm_recoup_f1` | Latest eval: recoup F1 |
| `poststorm_eval_docs` | Latest eval: document count |

The last five gauges are omitted if no report has been written yet.

### Configuration knobs

| Var | Default | Purpose |
|---|---|---|
| `EVAL_DIR` | `./data/eval` | Directory where `report.json` is written and read. |
| `EVAL_MONEY_TOLERANCE_CENTS` | `0` | Tolerance for money-field comparisons (0 = exact match to the cent). |

### Extension points (not built)

The following are intentionally out of scope for this demo build:

- **Metrics time-series store / Grafana** — `GET /metrics` is a point-in-time snapshot; there is no scrape target, Prometheus instance, or Grafana dashboard.
- **In-process latency histograms** — extraction and reconcile durations are not instrumented; `GET /metrics` exposes only counts and the latest eval scores.
- **Per-tenant metric labels** — all gauges are aggregate across tenants; there are no `tenant=` labels.

---

## How it works

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the data flow, module boundaries, the recoupment-detection logic, and the
production posture (security, scalability limits, deployability).

Short version: a thin FastAPI **I/O shell** streams a dual-provider race over SSE; the **pure** `reconcile.py` core does
all the ledger math (the LLM only extracts), so the catastrophic-exception-prone matching is deterministic and unit-tested
— and every extracted line carries a `source_span` citation for auditability.
