from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.ledger.models import Event
from backend.writeback.models import Delivery
from backend.writeback.payload import idempotency_key


def active_destinations(settings) -> list[str]:
    """Configured destinations, dropping any that aren't usable (webhook with no URL)."""
    dests = [x.strip() for x in settings.writeback_destinations.split(",") if x.strip()]
    if not settings.writeback_webhook_url:
        dests = [d for d in dests if d != "webhook"]
    return dests


def enqueue_pending(session, destinations: list[str], limit: int = 500) -> int:
    """For each destination, create a pending Delivery for every Event that has no Delivery yet.
    Idempotent: the (tenant_id, event_id, destination) unique constraint makes a re-run a no-op."""
    created = 0
    for dest in destinations:
        already = select(Delivery.event_id).where(Delivery.destination == dest)
        events = (session.query(Event).filter(Event.id.notin_(already))
                  .order_by(Event.id).limit(limit).all())
        for ev in events:
            session.add(Delivery(tenant_id=ev.tenant_id, event_id=ev.id, destination=dest,
                                 status="pending", idempotency_key=idempotency_key(ev.tenant_id, ev.id, dest)))
            try:
                session.commit()
                created += 1
            except IntegrityError:
                session.rollback()  # raced with another relay; the unique constraint held
    return created
