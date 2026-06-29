import hashlib
import json

from backend.ledger.models import Account, Entry


def idempotency_key(tenant_id: str, event_id: int, destination: str) -> str:
    return hashlib.sha256(f"{tenant_id}|{event_id}|{destination}".encode()).hexdigest()[:32]


def build_posting(session, event, destination: str) -> dict:
    meta = json.loads(event.meta or "{}")
    entries, debit_cents, claim_id, check_number = [], 0, None, None
    for e in session.query(Entry).filter_by(event_id=event.id).order_by(Entry.id).all():
        acc = session.get(Account, e.account_id)
        atype = acc.type if acc else ""
        akey = acc.key if acc else ""
        entries.append({"account_type": atype, "account_key": akey,
                        "direction": e.direction, "amount_cents": e.amount_cents, "reason": e.reason})
        if e.direction == "debit":
            debit_cents += e.amount_cents
        if atype == "claim":
            claim_id = akey
        if atype == "dump_account":
            check_number = akey
    return {
        "idempotency_key": idempotency_key(event.tenant_id, event.id, destination),
        "event_id": event.id, "tenant": event.tenant_id, "type": event.type, "batch": event.batch_id,
        "payer": meta.get("payer"), "patient_ref": meta.get("patient"),
        "claim_id": claim_id, "check_number": check_number,
        "offset_original_claim": meta.get("offset_original_claim"),
        "amount_cents": debit_cents,
        "entries": entries,
        "model": event.model, "confidence": event.confidence,
        "source_line_key": event.source_line_key,
        "posted_at": event.created_at.isoformat() if event.created_at else None,
    }


def to_835(posting: dict) -> str:
    """A simplified, representative 835-style remittance — NOT standards-valid X12.
    The full X12 835 generator is a documented file-adapter extension point."""
    dollars = posting.get("amount_cents", 0) / 100
    payer = posting.get("payer") or "PAYER"
    lines = [
        "# Simplified 835-style ERA (representative, not standards-valid X12)",
        f"BPR*I*{dollars:.2f}*C*ACH*{posting.get('type', '').upper()}",
        f"TRN*1*{posting.get('check_number') or posting.get('idempotency_key')}*{payer}",
        f"N1*PR*{payer}",
        "N1*PE*PROVIDER",
        f"CLP*{posting.get('claim_id') or '-'}*{posting.get('type')}*{dollars:.2f}*{dollars:.2f}*"
        f"{posting.get('patient_ref') or '-'}",
        f"SVC*{posting.get('claim_id') or '-'}*{dollars:.2f}*{dollars:.2f}",
    ]
    if posting.get("offset_original_claim"):
        # PLB = provider-level adjustment; WO = overpayment recovery (the recoup/takeback)
        lines.append(f"PLB*PROVIDER*WO*{posting['offset_original_claim']}*{dollars:.2f}")
    return "\n".join(lines) + "\n"
