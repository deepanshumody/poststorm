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

## How it works

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the data flow, module boundaries, the recoupment-detection logic, and the
production posture (security, scalability limits, deployability).

Short version: a thin FastAPI **I/O shell** streams a dual-provider race over SSE; the **pure** `reconcile.py` core does
all the ledger math (the LLM only extracts), so the catastrophic-exception-prone matching is deterministic and unit-tested
— and every extracted line carries a `source_span` citation for auditability.
