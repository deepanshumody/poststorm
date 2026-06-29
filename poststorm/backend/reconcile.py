from dataclasses import dataclass, field

from backend.schema import EventType, LineItem


@dataclass
class LedgerEntry:
    entity_type: str
    entity_id: str
    direction: str
    amount: float
    reason: str
    linked_claim: str | None = None
    dump_account_id: str | None = None
    source_claims: list = field(default_factory=list)


@dataclass
class Recoup:
    recoup_claim_id: str
    original_claim_id: str | None
    cross_patient: bool
    dump_account_id: str | None
    amount: float
    status: str  # "matched" | "needs_review"
    candidates: list = field(default_factory=list)


@dataclass
class ReconcileResult:
    posting_rows: list
    recoups: list
    ledger: list
    needs_review: list
    totals: dict


def _is_recoup(li: LineItem) -> bool:
    return li.event_type in (EventType.recoup, EventType.reversal) or li.recoup_flag or li.paid < 0


def reconcile(line_items: list[LineItem]) -> ReconcileResult:
    rows = list(line_items)
    payments = [li for li in rows if not _is_recoup(li) and li.paid > 0]
    recoup_lines = [li for li in rows if _is_recoup(li)]

    ledger: list[LedgerEntry] = []
    recoups: list[Recoup] = []
    needs_review: list[LineItem] = []

    # Plain payments → credit the claim.
    for p in payments:
        ledger.append(LedgerEntry("claim", p.claim_id, "credit", p.paid,
                                  "payment posted", p.claim_id, None, [p.claim_id]))

    for rl in recoup_lines:
        amt = abs(rl.paid) if rl.paid else abs(rl.adjustment)
        cand = [p for p in payments if p.payer == rl.payer and abs(p.paid - amt) < 0.005]
        same = [c for c in cand if c.patient_ref == rl.patient_ref]
        cross = [c for c in cand
                 if c.patient_ref != rl.patient_ref and c.check_number == rl.check_number]

        if len(same) == 1:
            orig = same[0]
            recoups.append(Recoup(rl.claim_id, orig.claim_id, False, None, amt, "matched"))
            ledger.append(LedgerEntry("claim", rl.claim_id, "debit", amt,
                                      "reversal of prior payment", orig.claim_id, None,
                                      [rl.claim_id, orig.claim_id]))
        elif not same and len(cross) == 1:
            orig = cross[0]
            dump = f"dump_{rl.check_number}"
            recoups.append(Recoup(rl.claim_id, orig.claim_id, True, dump, amt, "matched"))
            # Two-row entry: B debited via dump account; A's payment stays intact.
            ledger.append(LedgerEntry("claim", rl.claim_id, "debit", amt,
                                      "cross-patient takeback (dump account)", orig.claim_id,
                                      dump, [rl.claim_id]))
            ledger.append(LedgerEntry("claim", orig.claim_id, "credit", orig.paid,
                                      "original payment intact", orig.claim_id, dump,
                                      [orig.claim_id]))
        else:
            recoups.append(Recoup(rl.claim_id, None, False, None, amt, "needs_review",
                                  [c.claim_id for c in cross]))
            needs_review.append(rl)

    totals = {
        "lines": len(rows),
        "payments": len(payments),
        "recoups_caught": sum(1 for r in recoups if r.status == "matched"),
        "needs_review": len(needs_review),
        "posted_amount": round(sum(p.paid for p in payments), 2),
    }
    return ReconcileResult(rows, recoups, ledger, needs_review, totals)
