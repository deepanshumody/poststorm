import time
from dataclasses import dataclass

import httpx

from backend import schema
from backend.config import get_settings

SYSTEM = (
    "You are an expert medical-billing remittance reader. Read this scanned EOB / "
    "remittance-advice image and extract EVERY line into the schema. Rules: "
    "(1) A negative paid amount, or wording like 'recoupment', 'offset', 'takeback', "
    "'reversal', 'overpayment recovered' => event_type='recoup' (or 'reversal') and "
    "recoup_flag=true. Normal payments => event_type='payment'. "
    "(2) Copy claim_id, payer, patient name (patient_ref), service_date, check_number, "
    "and the dollar columns exactly as printed. "
    "(3) source_span must quote the exact text you read for that line. "
    "(4) Use confidence='low' when the scan is unreadable; never invent values."
)


@dataclass
class ExtractionResult:
    line_items: list
    time_info: dict
    usage: dict
    raw: dict
    wall_ms: float


def extract_page(image_data_uri: str, client: httpx.Client | None = None) -> ExtractionResult:
    s = get_settings()
    owns = client is None
    client = client or httpx.Client(timeout=90)
    try:
        t0 = time.perf_counter()
        r = client.post(
            f"{s.cerebras_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {s.cerebras_api_key}"},
            json={
                "model": s.cerebras_model,
                "reasoning_effort": "none",
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Extract all remittance lines from this EOB."},
                        {"type": "image_url", "image_url": {"url": image_data_uri}},
                    ]},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "line_items", "strict": True,
                                    "schema": schema.RESPONSE_SCHEMA},
                },
            },
        )
        wall_ms = (time.perf_counter() - t0) * 1000
        r.raise_for_status()
        j = r.json()
        items = schema.parse_line_items(j["choices"][0]["message"]["content"])
        return ExtractionResult(items, j.get("time_info", {}), j.get("usage", {}), j, wall_ms)
    finally:
        if owns:
            client.close()
