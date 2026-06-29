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


def _post_recoup(session, tenant_id, batch_id, line, r, lk):
    cents = abs(to_cents(line.paid))
    ev = _event(session, tenant_id, batch_id, "recoup", line, lk,
                {"payer": line.payer, "offset_original_claim": r.original_claim_id, "patient": line.patient_ref})
    _entry(session, ev, _account(session, tenant_id, "claim", line.claim_id), "debit", cents, "prior payment reversed")
    _entry(session, ev, _account(session, tenant_id, "dump_account", line.check_number), "credit", cents, "parked offset")


def _post_reversal(session, tenant_id, batch_id, line, lk):
    cents = abs(to_cents(line.paid))
    ev = _event(session, tenant_id, batch_id, "reversal", line, lk,
                {"payer": line.payer, "patient": line.patient_ref})
    _entry(session, ev, _account(session, tenant_id, "claim", line.claim_id), "debit", cents, "reversal")
    _entry(session, ev, _account(session, tenant_id, "provider_cash", "main"), "credit", cents, "cash reduced")


def _exception(session, tenant_id, lk, kind, line):
    session.add(ReviewException(tenant_id=tenant_id, line_key=lk, kind=kind,
                payload=json.dumps({"claim": line.claim_id, "patient": line.patient_ref, "paid": line.paid})))
    session.add(PostedLine(tenant_id=tenant_id, line_key=lk, event_id=None))


def _route_recoup(session, tenant_id, batch_id, line, r, lk, res):
    if r and r.cross_patient:
        _post_recoup(session, tenant_id, batch_id, line, r, lk)
        res.events += 1
        res.posted += 1
    elif r and not r.cross_patient:
        _post_reversal(session, tenant_id, batch_id, line, lk)
        res.events += 1
        res.posted += 1
    else:
        _exception(session, tenant_id, lk, "ambiguous", line)
        res.exceptions += 1


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


def balances(session, tenant_id) -> dict:
    accts = session.query(Account).filter_by(tenant_id=tenant_id).all()
    cash = sum(-a.balance_cents for a in accts if a.type == "provider_cash")   # payments debit cash
    dump = sum(a.balance_cents for a in accts if a.type == "dump_account")
    claims = {a.key: a.balance_cents for a in accts if a.type == "claim"}
    payer: dict[str, int] = {}
    for ev in session.query(Event).filter_by(tenant_id=tenant_id, type="recoup").all():
        p = json.loads(ev.meta).get("payer", "?")
        deb = session.query(Entry).filter_by(event_id=ev.id, direction="debit").first()
        payer[p] = payer.get(p, 0) + (deb.amount_cents if deb else 0)
    return {
        "cash_received_cents": cash,
        "dump_exposure_cents": dump,
        "provider_net_cents": cash - dump,
        "claim_count": len(claims),
        "dump_account_count": sum(1 for a in accts if a.type == "dump_account"),
        "payer_recoups_cents": payer,
        "event_count": session.query(Event).filter_by(tenant_id=tenant_id).count(),
    }


def audit_trail(session, tenant_id, limit=50) -> list[dict]:
    out = []
    for ev in session.query(Event).filter_by(tenant_id=tenant_id).order_by(Event.id.desc()).limit(limit):
        entries = session.query(Entry).filter_by(event_id=ev.id).all()
        out.append({
            "id": ev.id, "type": ev.type, "batch": ev.batch_id, "model": ev.model,
            "confidence": ev.confidence, "source_span": ev.source_span, "meta": json.loads(ev.meta),
            "entries": [{"account_id": e.account_id, "dir": e.direction,
                         "cents": e.amount_cents, "reason": e.reason} for e in entries],
        })
    return out


def rebuild_projections(session, tenant_id) -> None:
    for a in session.query(Account).filter_by(tenant_id=tenant_id).all():
        a.balance_cents = 0
    session.flush()
    for e in session.query(Entry).join(Event, Entry.event_id == Event.id).filter(Event.tenant_id == tenant_id):
        acc = session.get(Account, e.account_id)
        acc.balance_cents += e.amount_cents if e.direction == "credit" else -e.amount_cents
    session.commit()
