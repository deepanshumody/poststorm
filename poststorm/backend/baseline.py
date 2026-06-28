"""GPU baseline: same multimodal extraction task on Google Gemini, for an honest
side-by-side latency comparison. Reasoning/thinking is disabled so it's a fair
speed test against Cerebras (which runs reasoning_effort=none)."""
import time

import httpx

from backend.config import get_settings

GEM_MODEL = "gemini-2.5-flash"
BASE = "https://generativelanguage.googleapis.com/v1beta"
PROMPT = ("Read this scanned EOB / remittance-advice image and list every remittance line as JSON: "
          "claim_id, patient, paid, event_type ('payment' or 'recoup'). A negative amount or "
          "'recoupment/offset/takeback' is event_type 'recoup'. Output only the JSON array.")


def extract_page_gemini(image_data_uri: str, max_retries: int = 3) -> dict:
    s = get_settings()
    if not s.gemini_api_key:
        return {"ok": False, "elapsed_ms": 0.0, "status": "no_key"}
    header, b64 = image_data_uri.split(",", 1)
    mime = header.split(";")[0].replace("data:", "") or "image/png"
    url = f"{BASE}/models/{GEM_MODEL}:generateContent"
    auth = {"x-goog-api-key": s.gemini_api_key}  # key in header, never in URL/logs
    body = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": mime, "data": b64}},
        ]}],
        "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}, "temperature": 0},
    }
    last = "error"
    for attempt in range(max_retries):
        try:
            t0 = time.perf_counter()
            r = httpx.post(url, json=body, headers=auth, timeout=90)
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                return {"ok": True, "elapsed_ms": ms, "status": "ok"}
            if r.status_code == 429:
                last = "quota"
                time.sleep(min(2 ** attempt, 6))
                continue
            return {"ok": False, "elapsed_ms": ms, "status": f"http_{r.status_code}"}
        except Exception:
            last = "exception"
    return {"ok": False, "elapsed_ms": 0.0, "status": last}
