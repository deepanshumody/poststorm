from backend import extract, images
from backend.config import get_settings
from backend.extract import ExtractionResult
from backend.schema import Confidence, EventType, LineItem
from tests._auth import authed_client


def _li():
    return LineItem(claim_id="C1", payer="Aetna", patient_ref="P-A", service_date="2026-01-01",
        carc=None, rarc=[], charge=100.0, allowed=80.0, paid=80.0, adjustment=20.0,
        patient_responsibility=0.0, event_type=EventType.payment, recoup_flag=False,
        offset_link=None, check_number="CHK1", confidence=Confidence.high, source_span="x")


def test_eval_report_404_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_dir", str(tmp_path))
    c = authed_client(role="viewer")
    assert c.get("/eval/report").status_code == 404


def test_eval_run_requires_reviewer():
    viewer = authed_client(role="viewer")
    assert viewer.post("/eval/run?count=1").status_code == 403
    viewer.headers.pop("Authorization")
    assert viewer.post("/eval/run?count=1").status_code == 401


def test_eval_run_writes_and_returns_report(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_dir", str(tmp_path))
    monkeypatch.setattr(images, "load_page_images", lambda p: ["img"])
    monkeypatch.setattr(images, "image_to_data_uri", lambda img, **kw: "x")
    monkeypatch.setattr(extract, "extract_page", lambda uri, **kw: ExtractionResult([_li()], {}, {}, {}, 1.0))
    rc = authed_client(role="reviewer")
    r = rc.post("/eval/run?count=2")
    assert r.status_code == 200
    body = r.json()
    assert "field_accuracy" in body and "recoup" in body and body["docs"] >= 1
    # the report was persisted and is now served by GET /eval/report
    assert rc.get("/eval/report").status_code == 200
