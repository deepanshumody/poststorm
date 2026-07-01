# PostStorm — Lockbox-to-Ledger

Scanned paper **EOBs** (Explanation of Benefits) → **Gemma-4 (31B) on Cerebras** extracts structured remittance lines → a deterministic engine catches cross-patient **recoupment "dump accounts"** → an event-sourced, double-entry **who-owes-who ledger** → human review where ambiguous → reliable **write-back** to downstream systems — all behind enterprise-grade multi-tenancy, security, durability, and observability, with a live speed race against a GPU baseline.

A guiding principle runs through the whole system: **the model does perception; deterministic code does the money.** Gemma-4 turns pixels into structured data; every dollar decision after that is made by auditable code, not the model.

## Why it matters

Healthcare providers lose real money to remittance recoupments buried across unrelated patients' EOBs: a payer claws back an overpayment for patient A by quietly netting it against a payment for patient B on the same check. Catching those cross-patient takebacks is a notorious revenue-cycle pain. PostStorm reads the scans with Gemma-4, reconciles them **deterministically** (the money math stays in code, never the model), surfaces every takeback into an auditable ledger and a human review queue, and writes the postings back to downstream systems.

## What's inside (`poststorm/`)

- **Vision extraction** — Gemma-4-31B on Cerebras reads scanned EOBs into strict JSON, with a live throughput race vs. a GPU baseline.
- **Deterministic reconciliation + event-sourced ledger** — append-only double-entry, integer cents, dump-account exposure, idempotent posting.
- **Human-in-the-loop review** — ambiguous / low-confidence lines route to a review queue; resolutions post to the ledger with reviewer provenance.
- **Enterprise auth & multi-tenancy** — per-tenant hashed API keys → short-lived JWTs, RBAC, tenant isolation, per-tenant rate limiting, append-only audit log.
- **Durable ingest pipeline** — upload → SQLite-backed queue → restart-survivable in-process workers → ledger (idempotent finalize, crash-recovery sweep).
- **Write-back / delivery** — an event-sourced outbox delivers each posting to a file export (JSON + a representative 835) and an HMAC-signed webhook, with at-least-once delivery, idempotency, retry, and dead-lettering.
- **Observability & evaluation** — a Prometheus `/metrics` endpoint (aggregate-only, never-500 scrape) plus a pure, deterministic eval harness that scores extraction field-accuracy (money to the cent), recoup precision / recall / F1, and confidence calibration against a labeled corpus.

See **[`poststorm/README.md`](poststorm/README.md)**, **[`poststorm/ARCHITECTURE.md`](poststorm/ARCHITECTURE.md)**, and **[`poststorm/ENTERPRISE.md`](poststorm/ENTERPRISE.md)** for full documentation.

## Run it

```bash
cd poststorm
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
# add your CEREBRAS_API_KEY (and optional GEMINI_API_KEY) to poststorm/.env
./.venv/bin/python -m uvicorn backend.main:app --port 8000
# open http://localhost:8000
```

## Live demo result (6 synthetic EOBs)

Gemma-4 on Cerebras extracted 6 documents in **3.3 s**, caught **2 cross-patient recoupments** and routed 1 ambiguous case to the review queue, then posted a 10-event ledger flagging **$576.81** of dump-account exposure — zero false positives. 172 automated tests. All data is synthetic; no PHI.
