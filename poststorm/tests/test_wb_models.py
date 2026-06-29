import pytest
from sqlalchemy.exc import IntegrityError

from backend.ledger.db import make_memory_session
from backend.writeback.models import Delivery


def test_delivery_roundtrip_and_defaults():
    s = make_memory_session()
    s.add(Delivery(tenant_id="demo", event_id=1, destination="file", idempotency_key="k1"))
    s.commit()
    d = s.query(Delivery).one()
    assert d.status == "pending" and d.attempts == 0 and d.delivered_at is None and d.payload_sha256 == ""


def test_delivery_unique_per_event_destination():
    s = make_memory_session()
    s.add(Delivery(tenant_id="demo", event_id=1, destination="file", idempotency_key="k1"))
    s.commit()
    s.add(Delivery(tenant_id="demo", event_id=1, destination="file", idempotency_key="k1"))
    with pytest.raises(IntegrityError):
        s.commit()
