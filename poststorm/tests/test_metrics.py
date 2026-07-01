from fastapi.testclient import TestClient

from backend import metrics
from backend.config import get_settings
from backend.eval import run as eval_run
from backend.ledger.db import make_memory_session
from backend.ledger.models import Account, Event
from backend.main import app
from backend.writeback.models import Delivery


def test_render_metrics_emits_db_gauges():
    s = make_memory_session()
    s.add(Event(tenant_id="demo", batch_id="b", type="payment", source_line_key="k"))
    s.add(Account(tenant_id="demo", type="dump_account", key="CK", balance_cents=5000))
    s.add(Delivery(tenant_id="demo", event_id=1, destination="file", status="delivered", idempotency_key="k"))
    s.commit()
    text = metrics.render_metrics(s)
    assert "# TYPE poststorm_ledger_events gauge" in text
    assert "poststorm_ledger_events 1" in text
    assert "poststorm_dump_exposure_cents 5000" in text
    assert 'poststorm_deliveries{status="delivered"} 1' in text


def test_render_metrics_includes_eval_gauges_when_report_present(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_dir", str(tmp_path))
    eval_run.write_report({"docs": 24, "field_accuracy": {"overall": 0.97},
                           "recoup": {"precision": 1.0, "recall": 0.75, "f1": 0.857}})
    s = make_memory_session()
    text = metrics.render_metrics(s)
    assert "poststorm_recoup_recall 0.75" in text and "poststorm_field_accuracy 0.97" in text


def test_metrics_endpoint_is_open_text_plain():
    c = TestClient(app)
    r = c.get("/metrics")   # no Authorization header
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "poststorm_ledger_events" in r.text
