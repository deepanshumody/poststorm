import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.ledger.models import Account, Entry, Event, PostedLine, ReviewException
from backend.ledger.money import to_cents
from backend.schema import EventType, LineItem


@dataclass
class PostingResult:
    posted: int = 0
    skipped: int = 0
    exceptions: int = 0
    events: int = 0
    dump_exposure_cents: int = 0


def line_key(tenant_id: str, line: LineItem) -> str:
    raw = f"{tenant_id}|{line.check_number}|{line.claim_id}|{line.patient_ref}|{to_cents(line.paid)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _account(session, tenant_id, type_, key):
    a = session.query(Account).filter_by(tenant_id=tenant_id, type=type_, key=key).first()
    if a is None:
        a = Account(tenant_id=tenant_id, type=type_, key=key, balance_cents=0)
        session.add(a)
        session.flush()
    return a


def _entry(session, event, account, direction, cents, reason):
    session.add(Entry(event_id=event.id, account_id=account.id, direction=direction,
                      amount_cents=cents, reason=reason))
    account.balance_cents += cents if direction == "credit" else -cents


def _event(session, tenant_id, batch_id, type_, line, lk, meta):
    conf = line.confidence.value if hasattr(line.confidence, "value") else str(line.confidence)
    ev = Event(tenant_id=tenant_id, batch_id=batch_id, type=type_, source_line_key=lk,
               model=get_settings().cerebras_model, confidence=conf,
               source_span=line.source_span, meta=json.dumps(meta))
    session.add(ev)
    session.flush()
    session.add(PostedLine(tenant_id=tenant_id, line_key=lk, event_id=ev.id))
    return ev


def _post_payment(session, tenant_id, batch_id, line, lk):
    cents = to_cents(line.paid)
    ev = _event(session, tenant_id, batch_id, "payment", line, lk,
                {"payer": line.payer, "patient": line.patient_ref})
    _entry(session, ev, _account(session, tenant_id, "provider_cash", "main"), "debit", cents, "payment received")
    _entry(session, ev, _account(session, tenant_id, "claim", line.claim_id), "credit", cents, "payment posted")


def _is_recoup(line: LineItem) -> bool:
    return line.recoup_flag or line.event_type in (EventType.recoup, EventType.reversal) or line.paid < 0


# Temporary stub — full implementation in Task 4.
def _route_recoup(session, tenant_id, batch_id, line, r, lk, res):
    res.skipped += 1


def post(session, tenant_id, batch_id, lines, recoups) -> PostingResult:
    res = PostingResult()
    matched = {r.recoup_claim_id: r for r in recoups if r.status == "matched"}
    for line in lines:
        lk = line_key(tenant_id, line)
        if session.query(PostedLine).filter_by(tenant_id=tenant_id, line_key=lk).first():
            res.skipped += 1
            continue
        try:
            if _is_recoup(line):
                _route_recoup(session, tenant_id, batch_id, line, matched.get(line.claim_id), lk, res)
            elif line.paid > 0:
                _post_payment(session, tenant_id, batch_id, line, lk)
                res.events += 1
                res.posted += 1
            else:
                res.skipped += 1
            session.commit()
        except IntegrityError:
            session.rollback()
            res.skipped += 1
    res.dump_exposure_cents = sum(
        a.balance_cents for a in session.query(Account).filter_by(tenant_id=tenant_id, type="dump_account").all()
    )
    return res
