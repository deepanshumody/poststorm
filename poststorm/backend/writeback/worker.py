import asyncio

from backend.config import get_settings
from backend.ledger import db as ledger_db
from backend.ledger.models import Event, _now
from backend.logging_config import get_logger
from backend.writeback import adapters, relay
from backend.writeback.models import Delivery
from backend.writeback.payload import build_posting

log = get_logger("poststorm.writeback.worker")


def claim_next(session, tenant_id: str | None = None) -> Delivery | None:
    q = session.query(Delivery.id).filter_by(status="pending")
    if tenant_id is not None:
        q = q.filter_by(tenant_id=tenant_id)
    row = q.order_by(Delivery.created_at, Delivery.id).first()
    if row is None:
        return None
    did = row[0]
    updated = (session.query(Delivery).filter_by(id=did, status="pending")
               .update({"status": "delivering", "attempts": Delivery.attempts + 1, "updated_at": _now()}))
    session.commit()
    if updated == 0:
        return None  # another worker won the race
    return session.get(Delivery, did)


def deliver_one(tenant_id: str | None = None) -> bool:
    """Claim and deliver one pending Delivery. Synchronous (runs in a worker thread)."""
    s = ledger_db.SessionLocal()
    try:
        d = claim_next(s, tenant_id)
        if d is None:
            return False
        settings = get_settings()
        ev = s.get(Event, d.event_id)
        if ev is None:
            d.status = "failed"
            d.last_error = "event_missing"
            d.updated_at = _now()
            s.commit()
            return True
        posting = build_posting(s, ev, d.destination)
        if d.destination == "file":
            res = adapters.deliver_file(posting, d.tenant_id, settings)
        elif d.destination == "webhook":
            res = adapters.deliver_webhook(posting, settings)
        else:
            res = adapters.DeliveryResult(False, False, "unknown_destination", "")
        if res.ok:
            d.status = "delivered"
            d.delivered_at = _now()
            d.payload_sha256 = res.payload_sha256
            d.last_error = None
        elif res.retryable:
            d.status = "dead" if d.attempts >= settings.writeback_max_attempts else "pending"
            d.last_error = res.detail[:200]
        else:
            d.status = "failed"
            d.last_error = res.detail[:200]
        d.updated_at = _now()
        s.commit()
        return True
    finally:
        s.close()


def recover_orphans(session) -> int:
    n = session.query(Delivery).filter_by(status="delivering").update({"status": "pending"})
    session.commit()
    return n


async def worker_loop(stop_event, idle_sleep: float | None = None) -> None:
    idle = idle_sleep if idle_sleep is not None else get_settings().writeback_idle_sleep
    while not stop_event.is_set():
        try:
            did = await asyncio.to_thread(deliver_one)
        except Exception:
            log.exception("writeback delivery iteration failed; continuing")
            did = False
        if not did:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=idle)
            except TimeoutError:
                pass


async def relay_loop(stop_event, idle_sleep: float | None = None) -> None:
    settings = get_settings()
    idle = idle_sleep if idle_sleep is not None else settings.writeback_idle_sleep
    dests = [x.strip() for x in settings.writeback_destinations.split(",") if x.strip()]

    def _relay():
        sess = ledger_db.SessionLocal()
        try:
            return relay.enqueue_pending(sess, dests)
        finally:
            sess.close()

    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_relay)
        except Exception:
            log.exception("writeback relay iteration failed; continuing")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=idle)
        except TimeoutError:
            pass
