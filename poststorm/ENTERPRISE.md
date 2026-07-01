# PostStorm — Enterprise Features

PostStorm is a healthcare revenue-cycle automation system: scanned **EOBs** (Explanation of Benefits) are read by **Gemma-4 on Cerebras**, reconciled by a **deterministic** engine that catches cross-patient **recoupment "dump accounts,"** posted to an **event-sourced ledger**, reviewed by humans where ambiguous, and **written back** to downstream systems — all behind enterprise-grade multi-tenancy, security, durability, and observability.

A guiding principle runs through the whole system: **the model does perception; deterministic code does the money.** Gemma-4 turns pixels into structured data; every dollar decision after that is made by auditable code, not the model.

This document walks through each enterprise capability: **what it is, why it matters, and how it's built.**

---

## 1. Multi-tenancy & enterprise authentication

**What.** Every request is authenticated and scoped to a tenant. Tenants authenticate with a per-tenant **API key** (`pk_<tenant>_<random>`, shown once, stored only as a salted `sha256` hash, rotatable and revocable) via an **OAuth2 client-credentials** exchange (`POST /auth/token`) that returns a short-lived **HS256 JWT** (`{sub: kid, tenant, role, iat, exp}`). The JWT travels in `Authorization: Bearer` on every data request.

**Why.** A real BPO serves many providers; their data must never mix, keys must rotate without downtime, and access must be least-privilege.

**How.**
- **RBAC** — three roles, `viewer < reviewer < admin`. Below threshold → `403`; missing/invalid/expired token → `401` with `WWW-Authenticate: Bearer`.
- **Tenant isolation** — every data query filters by `principal.tenant`. A request for another tenant's specific resource returns **`404`** (never confirming the resource exists). The data layer is tenant-scoped end to end; isolation is enforced at both the endpoint and the service layer.
- **Per-tenant rate limiting** — an in-memory token bucket (default 60 burst / 5 rps) keyed by tenant → `429` + `Retry-After`; `/auth/token` is additionally throttled per source IP to blunt brute force.
- **Programmatic onboarding** — `POST /admin/tenants` (admin) provisions a tenant and returns its first key once; `POST /admin/tenants/{id}/keys` rotates; `DELETE /admin/keys/{kid}` revokes.
- **Append-only audit log** — every mutating request and token issuance is recorded (tenant, principal, action, resource, status, timestamp), exposed via `GET /admin/audit` (admin, tenant-scoped).

**Config:** `JWT_SECRET`, `JWT_TTL_SECONDS`, `SEED_TENANTS`, `RATE_BURST`/`RATE_RPS`, `ADMIN_BOOTSTRAP_KEY`, `DEMO_MODE`.

---

## 2. Event-sourced, double-entry ledger (system of record)

**What.** The financial truth is an **append-only event log**. Each posting is an immutable `Event` (payment / recoup / reversal / correction) with provenance (model, confidence, source span) and balanced **double-entry** `Entry` rows (debit/credit). Account balances are a **projection** rebuilt from the events.

**Why.** In RCM you must be able to prove *why* every dollar moved and reconstruct state at any point — append-only event sourcing gives a tamper-evident, fully auditable system of record.

**How.**
- **Integer cents** throughout — no floating-point money errors.
- **Idempotent posting** — a `PostedLine` unique key (`tenant | check | claim | patient | amount`) means re-posting the same remittance line is a safe no-op; the same batch can be replayed without double-counting.
- **Cross-patient recoupment detection** — the deterministic reconciler matches payer takebacks that are netted against *unrelated* patients on the same check, parks them in a **dump account**, and surfaces the **exposure** (the money a provider is silently losing).
- **Auditability** — `GET /ledger/balances` (system-of-record summary) and `GET /ledger/audit` (the event trail with entries + provenance).

---

## 3. Human-in-the-loop review

**What.** Lines the engine cannot resolve confidently — **ambiguous** recoups (an amount that could offset two different patients) and **low-confidence** extractions — are routed to a **review queue** instead of being auto-posted.

**Why.** Enterprise automation needs a safe fallback: surface the uncertain cases to a human rather than guess, and keep a record of who decided what.

**How.**
- A `ReviewException` captures the line, the reason, and the candidate matches.
- A reviewer resolves via four actions — **approve** (post as-is), **pick** (choose which candidate a recoup offsets), **correct** (fix a value), **dismiss** — through `POST /review/{id}/resolve`.
- Resolutions post to the ledger **with reviewer provenance** (the authenticated principal becomes the event's `reviewer`), and **corrections become `Feedback`** — the seam for continuous model/pipeline improvement.
- `GET /review/queue` and `GET /review/feedback` expose the work and the learning signal.

---

## 4. Durable intake & extraction pipeline

**What.** Documents are **uploaded** (PDF / PNG / JPG) or seeded, persisted, and processed by a pool of **in-process workers** off a durable queue — not inline in the request.

**Why.** A request-coupled pipeline loses work on a dropped connection or a restart. Enterprise ingestion must be **restart-survivable**, retry transient failures, and never lose or double-process a document.

**How.**
- Each document is a durable work-item with an **atomic claim** (a guarded `pending → processing` transition; no two workers claim the same row).
- Extraction reuses the Cerebras pipeline with **per-document retry/backoff**; a failed doc is dead-lettered, not fatal.
- **Idempotent finalize** keyed on "not yet posted," plus a **startup rescue sweep** that finalizes any job whose documents are all terminal but which a crash left unfinalized — so a job can never get stuck forever.
- **Partial-failure tolerance** — a job finalizes on its successful subset; unmatched recoups route to the review queue; failed docs are reprocessable via `POST /ingest/documents/{id}/retry`.
- **Hermetic & restart-safe** — workers live in the app lifespan with crash recovery; SQLite is tuned with `busy_timeout` + WAL for concurrent workers.
- Surfaces: `POST /documents` (upload), `GET /ingest/jobs/{id}` (status), `GET /ingest/jobs/{id}/stream` (live SSE).

**Config:** `INGEST_WORKERS`, `INGEST_MAX_ATTEMPTS`, `MAX_UPLOAD_MB`, `UPLOAD_DIR`, `INGEST_IDLE_SLEEP`.

---

## 5. Reliable write-back / delivery

**What.** Every posted ledger event is reliably delivered to downstream systems — a **file export** (canonical JSON + a representative 835-style remittance) and an **HMAC-signed webhook**.

**Why.** Posting to your ledger is only half the job; the result must reach the billing/source system reliably, exactly once in effect, even across outages.

**How.**
- A **transactional outbox**: a relay projects a durable `Delivery` per (event, destination) from the event log — the ledger itself is never modified (write-back is a pure *observer*).
- **At-least-once delivery with a stable idempotency key** (`sha256(tenant|event|destination)`): the webhook sends it as an `Idempotency-Key` header, the file adapter writes to a deterministic path, and the receiver dedups — so a retry after a crash is a no-op at the destination (**exactly-once effect**).
- **Atomic single-claim** (no double-deliver), **retry/backoff → dead-letter**, and **restart recovery** (`delivering → pending` on startup).
- **HMAC signing** — `X-Signature: sha256=HMAC-SHA256(secret, body)` over the exact bytes sent; the receiver verifies with a constant-time compare.
- Surfaces: `GET /writeback/deliveries`, `POST /writeback/deliveries/{id}/retry`, and a dev-only HMAC mock sink for end-to-end demos.

**Config:** `WRITEBACK_DESTINATIONS`, `WRITEBACK_WEBHOOK_URL`, `WRITEBACK_WEBHOOK_SECRET`, `WRITEBACK_MAX_ATTEMPTS`, `WRITEBACK_WORKERS`, `EXPORT_DIR`.

---

## 6. Observability & evaluation

**What.** A **Prometheus `/metrics`** endpoint and an **eval harness** that scores pipeline quality against ground truth.

**Why.** Enterprise systems must be measurable on two axes: *is it running well* (ops) and *is it correct* (quality). For an AI pipeline, continuously measuring extraction accuracy and recoup catch-rate is the differentiator.

**How.**
- **`GET /metrics`** (Prometheus exposition, open like `/health`, aggregate-only — no PHI/tenant data): ledger events, dump-account exposure, and review-queue / deliveries / ingest-jobs / documents counts by status, plus the latest eval scores as gauges.
- **Eval harness** — a pure, deterministic scorer compares the pipeline's output to a labeled corpus: extraction **field-accuracy** (money matched to the cent), recoup detection **precision / recall / F1**, and **confidence calibration** (do low-confidence flags actually predict errors?). Run it live via `POST /eval/run` (reviewer) or the `python -m backend.eval.run` CLI; read the latest report via `GET /eval/report`.

---

## 7. Security posture

- **Secrets** live only in a gitignored `.env`; nothing sensitive is committed.
- **No PHI** — all data is synthetic (generated locally); the system is designed so real PHI never needs to be committed or logged.
- **Redacted errors** — raw exceptions and secrets are logged server-side only; the wire gets a generic code. No secret, HMAC, webhook URL, or payload body ever appears in a response.
- **Transport hardening** — CORS allowlist, security headers (`nosniff`, `X-Frame-Options: DENY`, a Content-Security-Policy, `Referrer-Policy`), JWTs only in headers (never URLs), single-use stream tickets for SSE.
- **Cryptographic hygiene** — salted key hashing, HMAC request signing, constant-time signature comparison, algorithm-pinned JWT verification.

---

## 8. Production hardening

- **Idempotency everywhere** — ledger postings, job finalization, and deliveries are all safe to retry/replay.
- **Restart survivability** — durable queues + startup recovery sweeps for both ingest and write-back; nothing critical lives only in memory.
- **12-factor config** — every knob is environment-overridable; sensible local defaults.
- **Bounded resources** — in-memory stores are capped with eviction; workers are bounded; SQLite tuned for concurrency.
- **Quality gates** — a comprehensive automated test suite (unit + integration, including tenant-isolation, idempotency, and concurrency tests), `ruff` linting, and CI.

---

## Endpoint map (by capability)

| Capability | Endpoints |
|---|---|
| Auth & admin | `POST /auth/token`, `GET /auth/whoami`, `POST /admin/tenants`, `POST /admin/tenants/{id}/keys`, `DELETE /admin/keys/{kid}`, `GET /admin/audit` |
| Ledger (system of record) | `GET /ledger/balances`, `GET /ledger/audit` |
| Human review | `GET /review/queue`, `POST /review/{id}/resolve`, `GET /review/feedback` |
| Durable ingest | `POST /documents`, `POST /documents/demo-batch`, `GET /ingest/jobs/{id}`, `POST /ingest/documents/{id}/retry`, `GET /ingest/jobs/{id}/stream` |
| Write-back | `GET /writeback/deliveries`, `POST /writeback/deliveries/{id}/retry` |
| Observability & eval | `GET /metrics`, `GET /eval/report`, `POST /eval/run` |
| Health | `GET /health` |

See **[`README.md`](README.md)** and **[`ARCHITECTURE.md`](ARCHITECTURE.md)** for setup and internals.
