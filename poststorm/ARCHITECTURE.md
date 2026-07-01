# PostStorm — Architecture

## Data flow

```
 scanned EOB (PNG/PDF)
        │
        ▼
 images.py ── PyMuPDF rasterize → Pillow downscale → base64 data URI
        │
        ▼
 jobs.py ── dual-provider race (asyncio), one pre-decoded image per doc fed to BOTH lanes
        ├───────────────► extract.py ── Cerebras gemma-4-31b, strict json_schema, 429 retry/backoff
        │                                   │  ExtractionResult(line_items, time_info, usage, wall_ms)
        │                                   ▼
        │                              schema.py ── strict LineItem (pydantic) + RESPONSE_SCHEMA + parser
        │                                   ▼
        │                              reconcile.py ── PURE deterministic engine:
        │                                   • classify payment / recoup
        │                                   • match cross-patient offsets (payer + |amount| + same check, diff patient)
        │                                   • build who-owes-who ledger (balances computed in code)
        └───────────────► baseline.py ── Google Gemini (GPU baseline, timing only)
        │
        ▼
 main.py ── FastAPI: SSE stream (start → doc* / gem* → ledger → cer_done → gem_done → done)
        ▼
 frontend/index.html ── single-file dashboard: scanner "reading head", batch wall,
                        posting grid + ledger, dump-account climax, live speed race
```

## Module boundaries (deliberate)

| Module | Responsibility | Depends on | I/O |
|---|---|---|---|
| `config.py` | env-driven settings (pydantic-settings) | — | reads env/.env |
| `schema.py` | the `LineItem` contract + strict JSON schema | pydantic | none |
| `images.py` | document → downscaled base64 | PyMuPDF, Pillow | reads files |
| `extract.py` | Cerebras Gemma‑4 multimodal extraction | httpx, schema, config | network |
| `baseline.py` | Gemini GPU baseline (timing) | httpx, config | network |
| **`reconcile.py`** | **pure** recoupment + ledger engine | schema only | **none** |
| `jobs.py` | async race orchestration → events | the above | network |
| **`auth.py`** | **API-key + JWT identity layer** (`Principal`, `require_role`, `issue_jwt`, `verify_api_key`) | PyJWT, models | DB (key lookup) |
| **`ratelimit.py`** | **per-tenant token-bucket rate limiter** (`RateLimiter`, `enforce` FastAPI dep) | auth, config | none |
| `main.py` | HTTP/SSE shell, validation, headers, audit middleware | jobs, auth, ratelimit, config | network |
| `logging_config.py` | structured stdlib logging | config | stderr |
| **`ledger/`** | **durable event-sourced double-entry ledger** | SQLAlchemy, schema | DB |
| `ledger/review.py` | review queue + resolve actions (approve / pick / correct / dismiss) | ledger/service, models | DB |
| `ingest/models.py` | `IngestJob`, `Document`, `Extraction` SQLAlchemy tables (share the ledger `Base`) | SQLAlchemy, ledger/models | DB |
| `ingest/storage.py` | upload validation (type 415 / size 413) + file persistence in tenant-scoped dirs | config | filesystem |
| `ingest/queue.py` | durable queue ops: `enqueue_job`, atomic `claim_next`, `record_extraction`, `mark_failed`, `maybe_finalize_job` | ingest/models, ledger/service, reconcile | DB |
| `ingest/worker.py` | `process_one` (claim → extract → record), `recover_orphans`, `worker_loop` (asyncio task) | queue, extract, images, config | DB, network |
| `writeback/models.py` | `Delivery` SQLAlchemy outbox table (shares the ledger `Base`); unique constraint `(tenant_id, event_id, destination)` | SQLAlchemy, ledger/models | DB |
| `writeback/payload.py` | `idempotency_key`, `build_posting` (canonical posting dict), `to_835` (representative ERA — not standards-valid X12) | ledger/models | none |
| `writeback/relay.py` | `enqueue_pending` — projects `Delivery` rows from the event log for each destination (idempotent) | writeback/models, writeback/payload, ledger/models | DB |
| `writeback/adapters.py` | `DeliveryResult`, `deliver_file` (JSON + `.835.txt`), `deliver_webhook` (HMAC-signed POST) | writeback/payload, config | filesystem, network |
| `writeback/worker.py` | `claim_next`, `deliver_one`, `recover_orphans`, `worker_loop`, `relay_loop` (asyncio tasks) | writeback/models, payload, relay, adapters, config | DB, filesystem, network |
| **`eval/groundtruth.py`** | load `ground_truth.json` → `{doc_id: doc}` dict; `planted_recoup_claims` → set of planted recoup claim ids | — | reads file |
| **`eval/score.py`** | pure scorer: `field_accuracy`, `recoup_metrics`, `confidence_calibration`, `build_report` | ledger/money, schema | **none** |
| **`eval/run.py`** | `run_eval` (extract corpus + reconcile + score), `write_report` / `read_report` (`EVAL_DIR/report.json`), `main` entry-point | eval/groundtruth, eval/score, extract, images, reconcile, config | filesystem, network (live run only) |
| **`metrics.py`** | `render_metrics(session) → str` — Prometheus text format: operational gauges from the DB + latest eval scores | SQLAlchemy models, eval/run | DB, filesystem (report read) |

**The core is pure, the shell is thin.** `reconcile.py` has no I/O and is the most heavily unit-tested module — the
catastrophic-exception-prone money math is deterministic, not model-driven. This mirrors the "deterministic agents win at
enterprise scale" thesis: *a 0.0001% exception rate is 100 exceptions on 1M transactions.* The LLM is confined to
strict-schema extraction; every line carries a `source_span` so a human can verify against the source image.

Extraction is **provider-agnostic** (OpenAI-compatible Chat Completions), so swapping models/providers is a config change.

## Ledger (system of record)

`backend/ledger/` is an **append-only, event-sourced double-entry ledger** that durably records every financial posting.

Key design decisions:

- **Append-only events** — rows are never updated or deleted; the audit trail is immutable.
- **Balanced double-entry** — every posting emits a debit entry and a matching credit entry; the two sides always sum to zero, so the ledger self-validates.
- **Rebuildable projections** — running balances (`account_balance`) are derived projections rebuilt from raw events; calling `rebuild_projections()` at any time regenerates them from scratch.
- **Idempotent on `line_key`** — each ledger line carries a deterministic composite key (tenant + check_number + claim_id + patient_ref + paid_cents — note: **not** the run/batch id, so the same line re-posted in a later batch is recognized as a duplicate and skipped via the `UNIQUE` constraint / `IntegrityError` handling).
- **Integer cents** — all amounts are stored as integer cents to avoid floating-point rounding drift.

Storage: SQLite by default (`data/ledger.db`, volume-mounted in Docker via `ledger-data`); Postgres via `DATABASE_URL`.  
Endpoints: `GET /ledger/balances` (current balances), `GET /ledger/audit` (raw event log).

## Human-in-the-loop review

Some lines cannot be safely auto-posted: a recoup whose amount matches more than one payment (**ambiguous**) and a
payment line the model marks **low-confidence**. Both are written as `ReviewException` rows
(kind = `ambiguous` | `low_confidence`) and held out of the main ledger until a human acts.

**Resolution** (`ledger/review.py`): four actions — **approve** (post as-is), **pick** (choose which candidate
payment an ambiguous recoup offsets), **correct** (edit a field and post the adjusted line), **dismiss** (drop
the line, post nothing). Resolved lines flow through `service.post_reviewed_line`, emitting a normal append-only
ledger event tagged with the `reviewer` string. Resolving an already-resolved exception is an idempotent no-op.

**Separation of concerns:** the `ReviewException` row is a **mutable work-item** (status `open → resolved /
dismissed`); the ledger event log remains immutable append-only fact. A line only becomes ledger fact once a
human (or the auto path for unambiguous high-confidence lines) signs off on it.

**Feedback seam:** every `correct` action records a `Feedback` row (original vs. corrected line + reviewer).
The pairs are stored for future use — not yet consumed for retraining.

Reviewer identity is the JWT `sub` claim (the issuing key's `kid`), passed to `service.post_reviewed_line` and recorded in the ledger event. See **Authentication & multi-tenancy** below.

## Authentication & multi-tenancy

**Identity lives at the edge.** `auth.py` supplies the `Principal(tenant, role, sub)` dataclass and two FastAPI dependencies — `require_principal` (validates the `Authorization: Bearer` JWT on every request) and `require_role(minimum)` (checks `viewer < reviewer < admin` and raises 403 if the caller's role is below the threshold). `ratelimit.enforce` wraps `require_principal` and enforces the per-tenant token bucket before the handler body runs.

**The data layer was already tenant-scoped.** Every ledger query, review-queue lookup, and job result is filtered by `principal.tenant`. Endpoints simply pass `principal.tenant` down; no handler reaches across tenant boundaries. A request for another tenant's resource returns 404 — the server does not leak the existence of foreign rows.

**Two distinct audit trails.** The `AuditLog` table (`auth.py` + the `audit_log` middleware in `main.py`) records *access events* — who called which mutating endpoint, from which key, with what HTTP outcome. This is independent of the ledger's `LedgerEvent` table, which records *money events* — financial postings and their balanced debit/credit entries. Access ≠ money; keeping the two logs separate prevents coupling between the security audit and the financial audit.

**JWTs in the header, never the URL.** The `Authorization: Bearer` header keeps tokens out of server logs, browser history, and referrer chains. The one exception is the EventSource stream (`GET /jobs/{jid}/stream`), which cannot carry a custom header from a browser — this endpoint uses a **single-use `stream_ticket`**: `POST /jobs` returns a short-lived opaque ticket; the client passes it as a URL query parameter; the server pops the ticket on first use (it is never valid a second time, and it binds to the specific job and tenant).

## Recoupment detection (the core insight)

Real payers represent takebacks as a **provider-level adjustment** (an 835 `PLB` "WO" segment / a "Specification of
Recoupment" block), not an inline negative claim. PostStorm flags a **cross-patient dump account** when a recoup line
(negative paid / recoup event) matches a payment line by **same payer + identical amount + same check number** but a
**different patient** — i.e. money clawed back on Patient B was silently netted against Patient A's payment inside one
$0.00 check. Ambiguous matches (same amount across patients) are routed to `needs_review`, never auto-linked.

## Production posture

**Security.** CORS locked to a configurable allowlist; security headers (`nosniff`, `DENY`, CSP, `Referrer-Policy`) on
every response; API keys passed via headers (never URLs/logs); request validation + bounds on `POST /jobs` (Pydantic
`Field(ge=1, le=48)` → clean 422, not a 500); model-extracted text is HTML-escaped before render (DOM-XSS defense) and
the strict JSON schema constrains it server-side; raw exceptions are logged server-side and redacted to a generic code
on the wire. **No PHI** anywhere — synthetic data only.

**Scalability.** Stateless request handling; per-lane concurrency caps with 429 retry/backoff; images pre-decoded once
and shared by both lanes; the in-memory job store is **bounded** (LRU-evicted) with a documented single-node limitation
(a durable queue/store is the obvious next step, intentionally omitted as over-engineering for this demo).

**Deployability.** One-command `docker compose up`; self-contained image (regenerates + validates fixtures at build,
non-root user, `/health` healthcheck); pinned dependencies; 12-factor env config; CI runs lint + the full test suite on
every push.

## Durable ingest pipeline

`backend/ingest/` is the SQLite-backed intake pipeline that runs alongside the synchronous race demo without
touching it.

**SQLite-backed durable queue.** `ingest/queue.py` provides the queue primitives. `enqueue_job` creates one
`IngestJob` + one `Document` per file, both `pending`. `claim_next` uses a predicate-guarded UPDATE
(`WHERE status='pending'`) so only one worker can claim a given document regardless of concurrency.
`maybe_finalize_job` is idempotent: it skips jobs already in a terminal state, and the ledger's `PostedLine`
`line_key` unique constraint absorbs any duplicate post attempt.

**Lifespan-managed workers.** `ingest/worker.py` exposes `worker_loop(stop_event)`, an asyncio task that calls
`process_one` in a thread (via `asyncio.to_thread`) to avoid blocking the event loop. Workers are created in
`main.py`'s `lifespan` context manager and cancelled at shutdown. A bare `TestClient(app)` (no `with`) does
**not** enter the lifespan, so the test suite runs with zero ingest workers — the suite stays hermetic.

**Partial-failure posts the successful subset.** When `maybe_finalize_job` runs and some documents failed,
it collects `Extraction` rows only from documents with status `extracted`, reconciles and posts those lines,
and marks the job `partially_failed`. Recoups whose counterpart payment lived in a failed document surface as
`ReviewException` rows in the D review queue rather than being silently dropped.

**Per-page multi-page extraction.** `process_one` iterates over pages returned by `images.load_page_images`,
calls `extract.extract_page` on each data-URI, and concatenates the resulting `LineItem` lists before writing
a single `Extraction` row.

**Orphan recovery.** On lifespan startup, `recover_orphans` resets every `Document` in state `processing`
back to `pending`. Documents in that state were being processed by a worker that crashed without completing;
the reset lets workers re-try them up to `INGEST_MAX_ATTEMPTS` times.

**Test coverage.** `tests/test_ingest_queue.py` verifies atomic claim (no double-claim under concurrency),
happy-path extraction, retry-then-fail, partial-failure subset posting, `recover_orphans`, and idempotent
finalize. `tests/test_ingest_worker.py` covers `process_one`. `tests/test_ingest_storage.py` covers upload
validation and tenant isolation. `tests/test_ingest_api.py` covers the upload endpoint, demo-batch, and the
retry endpoint. `tests/test_ingest_lifespan.py` verifies that workers drain the queue under a full lifespan
context and confirms a bare `TestClient` starts none.

## Write-back / delivery

`backend/writeback/` is the event-sourced delivery layer that observes the append-only ledger event log and forwards
postings to configured downstream destinations.

**Outbox projection, not direct coupling.** The relay (`relay.py`) scans `Event` rows for any that have no `Delivery`
row yet for a given destination, and projects one `Delivery` per event per destination into the outbox table. Layers
C (reconcile), D (review), F (ingest), and A+B (ledger + auth) are entirely untouched — write-back is a read-only
observer of the event log.

**At-least-once delivery with a stable idempotency key.** The key — `sha256(tenant|event_id|destination)` — is
deterministic and stable: the file path on disk is deterministic (a re-delivered file overwrites an identical one),
the `Idempotency-Key` webhook header lets the receiver deduplicate, and the outbox unique constraint
`(tenant_id, event_id, destination)` prevents double-enqueue regardless of relay concurrency.

**Same durable-worker patterns as the ingest layer.** Workers are lifespan-managed asyncio tasks (relay_loop +
worker_loop). `claim_next` uses a predicate-guarded UPDATE (`WHERE status='pending'`) so no two workers claim the
same delivery. `recover_orphans` resets `delivering → pending` on startup for crash recovery. A bare
`TestClient(app)` (no `with`) skips the lifespan entirely, keeping the test suite hermetic.

**Representative 835, not standards-valid X12.** `to_835` produces a human-readable ERA-style remittance
(BPR / TRN / N1 / CLP / SVC / PLB segments) for demo visibility. The file is labeled in-content as not standards-valid;
a full X12 835 EDI generator (certified library, proper loop/segment-count envelopes) is a documented extension point.

## Observability & eval

**Track G is read-only and additive.** `backend/eval/` and `backend/metrics.py` observe the existing DB and reuse `extract.py` + `reconcile.py` as-is — the ledger, ingest pipeline, review queue, and write-back layer are entirely untouched.

**Pure scorer.** `eval/score.py` has no I/O: given extracted `LineItem` lists and a ground-truth dict it returns a plain dict. It is deterministic and unit-tested in isolation. The two key scoring definitions:

- **Recall** counts both `matched` and `needs_review` recoup claims as detected (routing a planted claim to the review queue is not a miss).
- **Precision** is computed over the auto-resolved `matched` set only (auto-resolved caught / all auto-resolved matched).

**Live runner.** `eval/run.py` extracts the fixture corpus via the same `extract.py` + `reconcile.py` stack used in production, scores the result, and writes `EVAL_DIR/report.json`. Per-document extraction failures are logged and skipped without aborting the run.

**Prometheus endpoint.** `GET /metrics` is open (no auth, like `GET /health`) and returns aggregate-only gauges — operational counts from the DB (ledger events, dump-account exposure, review/delivery/ingest/document counts by status) plus the latest eval scores read from `EVAL_DIR/report.json`. There are no per-tenant labels and no PHI. The eval gauges are omitted if no report file exists yet.

**No pipeline instrumentation.** There are no in-process latency histograms, no metrics push from the request path, and no Grafana / time-series store. `GET /metrics` is a point-in-time snapshot derived entirely from existing DB tables and the report file.

## Tests

`tests/` covers the schema, the pure reconcile engine (normal / same-patient reversal / cross-patient dump account /
ambiguous → needs_review), the image pipeline, the FastAPI surface (validation 422s, 404s, security headers), and the
async orchestration (event sequence + that raw errors never reach the client) — all mocked, no live API calls.
