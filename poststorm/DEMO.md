# PostStorm — demo script

Two ways to show PostStorm: a **60-second video cut** (the speed-and-catch story) and a **5-minute live walkthrough**
(the full enterprise pipeline). Everything is already built — the demo is mostly "click **Read EOB batch** and
narrate." The batch (24 docs, 4 payer formats) finishes in ~12s, so a single take fits.

## Before you record (or present)

- Start the app: `uvicorn backend.main:app --port 8000`, open **http://localhost:8000**, and **load it once** so fonts +
  document thumbnails are cached (avoids first-load jank).
- Browser at 1920×1080, full-screen the page, hide the bookmarks bar and any notifications/extensions.
- **No PHI / no secrets on screen** — the data is synthetic (the page shows a `SYNTHETIC · NO PHI` chip; keep it visible).
- Capture: macOS `Cmd+Shift+5` (or OBS/Loom). Record system audio off; narrate in voiceover or add captions after.
- **Side-by-side note:** the Gemini lane needs free-tier quota. If it's exhausted, the race shows the honest
  "baseline unavailable — Cerebras finished before it could keep up" verdict — still a fine shot. To show the live race,
  record after the daily Gemini quota resets (or enable billing on that key).
- Optional: do one practice run so you know when the red **RECOUPMENT** card fires (at the end, after the wall fills).

## The 60-second cut (timed shot list)

| Time | On screen | Voiceover / caption |
|---|---|---|
| **0–6s** | Slowly scroll one scanned EOB (open `data/eobs/eob_004.png` or the reading-head), showing the messy Medicare remittance + the "WO" overpayment-recovery row. | "Healthcare billing teams drown in scanned paper remittances. The worst hidden loss: **recoupments** — a payer clawing back money on one patient by offsetting another's, inside the same check. Almost no posting system catches it." |
| **6–10s** | Cut to the PostStorm dashboard. Hover the **Read EOB batch** button, then click. | "PostStorm reads them with **Gemma-4 on Cerebras**." |
| **10–30s** | The wave: documents ignite green on the **batch wall**, the **reading head** beams across each scan, the **posting grid + ledger** fill, the **race bars** move (Cerebras shooting ahead). Let the timer + `tok/s` climb. | "Twenty-four scanned remittances — four different payer formats — read, posted, and reconciled in seconds. Over **1,500 tokens a second** on Cerebras, while the GPU baseline is still on document three." |
| **30–42s** | The **red RECOUPMENT card** fires; the detection band sits on the takeback row of the scanned doc. Pause here. | "And it catches the dump account a human poster misses — an **$845 takeback hiding across $0.00 recovery rows** — and reconciles it into a who-owes-who ledger." |
| **42–52s** | Cut to a metrics card/overlay (use the README Results numbers, or screen-text them). | "The built-in eval harness scores it: **100% recall, 100% precision** on recoupment detection — because the money math is deterministic code, not the model. Synthetic data, no PHI." |
| **52–60s** | Back to the full dashboard / the verdict bar ("Cerebras Nx faster"). End on the wordmark. | "PostStorm — **Lockbox-to-Ledger**. The posting problem, solved end to end." |

### The three beats that must land
1. **Speed** — the wall igniting + the race bar + the timer. This is the inference story; let it breathe for a few seconds.
2. **The catch** — the red RECOUPMENT card. This is the money moment; pause and let viewers read it.
3. **Trust** — "100% recall/precision, deterministic, no PHI." This is the close: the pipeline is **measurable** (a real
   eval harness, not a claim) and the money math is **auditable code**.

## The 5-minute live walkthrough (technical audience)

Run the 60-second flow first — it earns attention — then tour the production architecture behind it:

1. **The ambiguous case → human review** (~60s). After the batch, open the **review queue**: one recoup couldn't be
   confidently cross-matched (two candidate claims), so the engine *refused to guess* and routed it to a human.
   Resolve it live with **pick** — the posting lands in the ledger with reviewer provenance, and the correction is
   captured as feedback. *Talking point: automation that knows when to stop is what makes it deployable.*
2. **The system of record** (~45s). `GET /ledger/audit` — every posting is an immutable event with provenance (model,
   confidence, source span) and balanced double-entry rows in integer cents. Re-run the batch: **zero double-posting**
   (idempotent by line key). *Talking point: you can prove why every dollar moved.*
3. **Durable ingest** (~45s). Upload a PDF/PNG at `POST /documents` — durable queue, atomic claims, retry/dead-letter,
   restart-survivable workers, live SSE progress. Kill the server mid-batch and restart it: the job finishes.
4. **Write-back** (~45s). `GET /writeback/deliveries` — every posted event is delivered downstream via a transactional
   outbox: file export (JSON + a representative 835) and an HMAC-signed webhook, at-least-once with a stable
   idempotency key. *Talking point: posting isn't done until the source system knows.*
5. **Observability & eval** (~60s). `GET /metrics` (Prometheus, aggregate-only) for ops; then `POST /eval/run` or
   `python -m backend.eval.run` for quality — field accuracy to the cent, recoup precision/recall/F1, and confidence
   calibration against the labeled corpus. *Talking point: you can't run an AI pipeline you can't measure.*
6. **Multi-tenancy & security** (~30s, verbal or `ENTERPRISE.md` on screen). Per-tenant hashed keys → short-lived JWTs,
   RBAC, tenant isolation (cross-tenant → 404), rate limits, append-only audit log.

See **`ENTERPRISE.md`** for the full capability map and **`ARCHITECTURE.md`** for internals.

## If you want a tighter cut
- Run a smaller batch for a snappier wall (POST `/jobs` with `{"count": 12}` from the console, or temporarily change the
  `count:24` in `frontend/index.html`), but 24 reads more impressive and still fits in 60s.
- The recoup card auto-dismisses after ~6s — start your "the catch" narration the instant it appears.
