import hashlib
import hmac

import httpx

from backend.config import get_settings
from backend.writeback import adapters

_POSTING = {"idempotency_key": "abc123", "type": "payment", "amount_cents": 5000,
            "payer": "Aetna", "patient_ref": "P-A", "claim_id": "C1", "check_number": "CK",
            "offset_original_claim": None, "entries": []}


def test_deliver_file_writes_json_and_835(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "export_dir", str(tmp_path))
    res = adapters.deliver_file(_POSTING, "demo", get_settings())
    assert res.ok and not res.retryable
    assert (tmp_path / "demo" / "abc123.json").exists()
    assert "representative" in (tmp_path / "demo" / "abc123.835.txt").read_text().lower()


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)


def test_deliver_webhook_signs_and_succeeds_on_200(monkeypatch):
    monkeypatch.setattr(get_settings(), "writeback_webhook_url", "https://sink.test/hook")
    monkeypatch.setattr(get_settings(), "writeback_webhook_secret", "s3cret")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["idem"] = request.headers.get("Idempotency-Key")
        seen["sig"] = request.headers.get("X-Signature")
        seen["body"] = request.content
        return httpx.Response(200)

    res = adapters.deliver_webhook(_POSTING, get_settings(), client=_client(handler))
    assert res.ok and not res.retryable
    assert seen["idem"] == "abc123"
    expected = "sha256=" + hmac.new(b"s3cret", seen["body"], hashlib.sha256).hexdigest()
    assert seen["sig"] == expected  # signature verifies over the exact body


def test_deliver_webhook_5xx_is_retryable_4xx_is_permanent(monkeypatch):
    monkeypatch.setattr(get_settings(), "writeback_webhook_url", "https://sink.test/hook")
    r5 = adapters.deliver_webhook(_POSTING, get_settings(),
                                  client=_client(lambda req: httpx.Response(503)))
    assert not r5.ok and r5.retryable
    r4 = adapters.deliver_webhook(_POSTING, get_settings(),
                                  client=_client(lambda req: httpx.Response(400)))
    assert not r4.ok and not r4.retryable


def test_deliver_webhook_no_url_is_permanent_fail(monkeypatch):
    monkeypatch.setattr(get_settings(), "writeback_webhook_url", "")
    res = adapters.deliver_webhook(_POSTING, get_settings())
    assert not res.ok and not res.retryable
