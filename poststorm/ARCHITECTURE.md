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

## Tests

`tests/` covers the schema, the pure reconcile engine (normal / same-patient reversal / cross-patient dump account /
ambiguous → needs_review), the image pipeline, the FastAPI surface (validation 422s, 404s, security headers), and the
async orchestration (event sequence + that raw errors never reach the client) — all mocked, no live API calls.
