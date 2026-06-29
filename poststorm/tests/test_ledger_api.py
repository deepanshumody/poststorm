from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_balances_endpoint_shape():
    r = client.get("/ledger/balances")
    assert r.status_code == 200
    j = r.json()
    for k in ("cash_received_cents", "dump_exposure_cents", "event_count", "payer_recoups_cents"):
        assert k in j


def test_audit_endpoint_shape():
    r = client.get("/ledger/audit?limit=5")
    assert r.status_code == 200
    assert "events" in r.json()


def test_audit_negative_limit_is_safe():
    r = client.get("/ledger/audit?limit=-5")
    assert r.status_code == 200
    assert "events" in r.json()
