# PostStorm — 60-second demo script

A shot-by-shot plan for the demo video (max 60s). Everything is already built — recording is mostly
"click **Read EOB batch** and narrate." The batch (24 docs, 4 payer formats) finishes in ~12s, so a single take fits.

## Before you record
- Start the app: `uvicorn backend.main:app --port 8000`, open **http://localhost:8000**, and **load it once** so fonts +
  document thumbnails are cached (avoids first-load jank).
- Browser at 1920×1080, full-screen the page, hide the bookmarks bar and any notifications/extensions.
- **No PHI / no secrets on screen** — the data is synthetic (the page shows a `SYNTHETIC · NO PHI` chip; keep it visible).
- Capture: macOS `Cmd+Shift+5` (or OBS/Loom). Record system audio off; narrate in voiceover or add captions after.
- **Side-by-side note:** the Gemini lane needs free-tier quota. If it's exhausted, the race shows the honest
  "baseline unavailable — Cerebras finished before it could keep up" verdict — still a fine shot. To show the live race,
  record after the daily Gemini quota resets (or enable billing on that key).
- Optional: do one practice run so you know when the red **RECOUPMENT** card fires (at the end, after the wall fills).

## Shot list (timed)

| Time | On screen | Voiceover / caption |
|---|---|---|
| **0–6s** | Slowly scroll one scanned EOB (open `data/eobs/eob_004.png` or the reading-head), showing the messy Medicare remittance + the "WO" overpayment-recovery row. | "Healthcare billing teams drown in scanned paper remittances. The worst hidden loss: **recoupments** — a payer clawing back money on one patient by offsetting another's, inside the same check. No system catches it." |
| **6–10s** | Cut to the PostStorm dashboard. Hover the **Read EOB batch** button, then click. | "PostStorm reads them with **Gemma-4 on Cerebras**." |
| **10–30s** | The wave: documents ignite green on the **batch wall**, the **reading head** beams across each scan, the **posting grid + ledger** fill, the **race bars** move (Cerebras shooting ahead). Let the timer + `tok/s` climb. | "Twenty-four scanned remittances — four different payer formats — read, posted, and reconciled in seconds. Over **1,500 tokens a second** on Cerebras, while the GPU baseline is still on document three." |
| **30–42s** | The **red RECOUPMENT card** fires; the detection band sits on the takeback row of the scanned doc. Pause here. | "And it catches the dump account a human poster misses — an **$842 takeback hiding in a $0.00 check** — and reconciles it into a who-owes-who ledger." |
| **42–52s** | Cut to a metrics card/overlay (use the README Results numbers, or screen-text them). | "**100% recall, 100% precision** on recoupment detection — because the money math is deterministic code, not the model. Synthetic data, no PHI." |
| **52–60s** | Back to the full dashboard / the verdict bar ("Cerebras Nx faster"). End on the wordmark. | "PostStorm — **Lockbox-to-Ledger**. The posting problem, solved at the speed of Cerebras." |

## The three beats that must land
1. **Speed** — the wall igniting + the race bar + the timer. This is the Cerebras story; let it breathe for a few seconds.
2. **The catch** — the red RECOUPMENT card. This is the enterprise "money moment"; pause and let viewers read it.
3. **Trust** — "100% recall/precision, deterministic, no PHI." This is the Production-Readiness / Technical-Excellence close.

## If you want a tighter cut
- Run a smaller batch for a snappier wall (POST `/jobs` with `{"count": 12}` from the console, or temporarily change the
  `count:24` in `frontend/index.html`), but 24 reads more impressive and still fits in 60s.
- The recoup card auto-dismisses after ~6s — start your "the catch" narration the instant it appears.
