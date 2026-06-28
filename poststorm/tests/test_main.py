from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health_shape():
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert "version" in j and "model" in j and "docs" in j


def test_security_headers_present():
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert "content-security-policy" in h


def test_jobs_valid_request():
    r = client.post("/jobs", json={"count": 3})
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 3 and len(j["docs"]) == 3 and "job_id" in j


def test_jobs_rejects_non_numeric():
    assert client.post("/jobs", json={"count": "abc"}).status_code == 422


def test_jobs_rejects_too_large():
    assert client.post("/jobs", json={"count": 999}).status_code == 422


def test_jobs_rejects_zero():
    assert client.post("/jobs", json={"count": 0}).status_code == 422


def test_stream_unknown_job_is_404():
    assert client.get("/jobs/deadbeef/stream").status_code == 404
