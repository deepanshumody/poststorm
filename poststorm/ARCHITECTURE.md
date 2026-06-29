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
| `main.py` | HTTP/SSE shell, validation, headers | jobs, config | network |
| `logging_config.py` | structured stdlib logging | config | stderr |
| **`ledger/`** | **durable event-sourced double-entry ledger** | SQLAlchemy, schema | DB |

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
