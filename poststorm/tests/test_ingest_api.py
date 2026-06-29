import io

from PIL import Image

from backend.config import get_settings
from tests._auth import authed_client


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (12, 12), (200, 180, 140)).save(buf, "PNG")
    return buf.getvalue()


def _upload(client, name="scan.png", data=None, ctype="image/png"):
    return client.post("/documents", files={"files": (name, data or _png_bytes(), ctype)})


def test_upload_creates_job_and_pending_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    rc = authed_client(role="reviewer", tenant="up_a")
    r = _upload(rc)
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"].startswith("j_") and body["stream_ticket"]
    assert len(body["documents"]) == 1 and body["documents"][0]["status"] == "pending"


def test_upload_requires_reviewer():
    viewer = authed_client(role="viewer", tenant="up_a")
    assert _upload(viewer).status_code == 403
    # no token at all → 401
    viewer.headers.pop("Authorization")
    assert _upload(viewer).status_code == 401


def test_upload_rejects_unsupported_type(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    rc = authed_client(role="reviewer", tenant="up_a")
    r = _upload(rc, name="notes.txt", data=b"hello", ctype="text/plain")
    assert r.status_code == 415


def test_job_status_is_tenant_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    a = authed_client(role="reviewer", tenant="up_a")
    job_id = _upload(a).json()["job_id"]
    b = authed_client(role="reviewer", tenant="up_b")
    assert b.get(f"/ingest/jobs/{job_id}").status_code == 404   # cross-tenant → 404
    assert a.get(f"/ingest/jobs/{job_id}").status_code == 200
