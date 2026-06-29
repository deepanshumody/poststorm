import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from backend.writeback.payload import to_835


@dataclass
class DeliveryResult:
    ok: bool
    retryable: bool
    detail: str
    payload_sha256: str


def _body_and_hash(posting: dict) -> tuple[bytes, str]:
    body = json.dumps(posting, sort_keys=True).encode()
    return body, hashlib.sha256(body).hexdigest()


def deliver_file(posting: dict, tenant_id: str, settings) -> DeliveryResult:
    body, digest = _body_and_hash(posting)
    try:
        d = Path(settings.export_dir) / tenant_id
        d.mkdir(parents=True, exist_ok=True)
        key = posting["idempotency_key"]
        (d / f"{key}.json").write_bytes(body)            # idempotent: deterministic path
        (d / f"{key}.835.txt").write_text(to_835(posting))
        return DeliveryResult(ok=True, retryable=False, detail="written", payload_sha256=digest)
    except OSError:
        return DeliveryResult(ok=False, retryable=True, detail="file_error", payload_sha256=digest)


def deliver_webhook(posting: dict, settings, client: httpx.Client | None = None) -> DeliveryResult:
    body, digest = _body_and_hash(posting)
    if not settings.writeback_webhook_url:
        return DeliveryResult(ok=False, retryable=False, detail="no_webhook_url", payload_sha256=digest)
    sig = hmac.new(settings.writeback_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"content-type": "application/json", "Idempotency-Key": posting["idempotency_key"],
               "X-Signature": f"sha256={sig}"}
    owns = client is None
    client = client or httpx.Client(timeout=10)
    try:
        r = client.post(settings.writeback_webhook_url, content=body, headers=headers)
        if r.status_code in (200, 201, 202, 204, 409):
            return DeliveryResult(ok=True, retryable=False, detail=f"http_{r.status_code}", payload_sha256=digest)
        if r.status_code >= 500:
            return DeliveryResult(ok=False, retryable=True, detail=f"webhook_{r.status_code}", payload_sha256=digest)
        return DeliveryResult(ok=False, retryable=False, detail=f"webhook_{r.status_code}", payload_sha256=digest)
    except httpx.HTTPError:
        return DeliveryResult(ok=False, retryable=True, detail="connect_error", payload_sha256=digest)
    finally:
        if owns:
            client.close()
