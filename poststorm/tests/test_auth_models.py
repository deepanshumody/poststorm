from backend.ledger.db import make_memory_session
from backend.ledger.models import ApiKey, AuditLog, Tenant


def test_new_tables_create_and_roundtrip():
    s = make_memory_session()
    s.add(Tenant(id="acme", name="Acme Health"))
    s.add(ApiKey(kid="k_1", tenant_id="acme", role="reviewer", key_hash="h", salt="s", active=True))
    s.add(AuditLog(tenant_id="acme", principal="k_1", action="job.create",
                   resource="/jobs", status_code=200))
    s.commit()

    assert s.get(Tenant, "acme").name == "Acme Health"
    k = s.get(ApiKey, "k_1")
    assert k.active is True and k.revoked_at is None
    assert s.query(AuditLog).filter_by(action="job.create").count() == 1
