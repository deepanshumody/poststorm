import time
from dataclasses import dataclass

import httpx

from backend import schema
from backend.config import get_settings

SYSTEM = (
    "You are an expert medical-billing remittance reader. Read this scanned EOB / "
    "remittance advice image and extract EVERY claim line into the schema.\n"
    "PAID: the 'paid' field is the amount PAID TO THE PROVIDER for that claim — usually "
    "the RIGHTMOST dollar column, labeled PAID, PROV-PD, Amount Paid, or Plan Paid. Do NOT "
    "confuse it with billed/charged, allowed, deductible, coinsurance, copay, or patient "
    "responsibility. Each payment's paid is a positive dollar amount.\n"
    "RECOUPMENTS: a provider-level adjustment / overpayment recovery / 'WO' / 'Specification "
    "of Recoupment' / 'Prior Account Adjustment' / a negative amount => event_type='recoup', "
    "recoup_flag=true, paid = the NEGATIVE amount. Extract the patient/account it names "
    "(this differs from the paid claims above).\n"
    "CHECK NUMBER: there is ONE check/EFT number per page (in the header) — copy that SAME "
    "check_number onto EVERY line, including recoupment lines.\n"
    "Also copy claim_id, payer, patient name (patient_ref), service_date, carc (group+code "
    "e.g. CO-45) and rarc. source_span must quote the text you read. Use confidence='low' "
    "only when truly unreadable; never invent values."
)


@dataclass
class ExtractionResult:
    line_items: list
    time_info: dict
    usage: dict
    raw: dict
    wall_ms: float


def extract_page(image_data_uri: str, client: httpx.Client | None = None,
                 max_retries: int = 6) -> ExtractionResult:
    s = get_settings()
    owns = client is None
    client = client or httpx.Client(timeout=90)
    payload = {
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
            "json_schema": {"name": "line_items", "strict": True, "schema": schema.RESPONSE_SCHEMA},
        },
    }
    headers = {"Authorization": f"Bearer {s.cerebras_api_key}"}
    try:
        last = None
        for attempt in range(max_retries):
            t0 = time.perf_counter()
            r = client.post(f"{s.cerebras_base_url}/chat/completions", headers=headers, json=payload)
            wall_ms = (time.perf_counter() - t0) * 1000
            if r.status_code == 429 or r.status_code >= 500:
                last = r
                wait = float(r.headers.get("retry-after", 0) or 0) or min(0.5 * (2 ** attempt), 8.0)
                time.sleep(wait + 0.05 * attempt)
                continue
            r.raise_for_status()
            j = r.json()
            items = schema.parse_line_items(j["choices"][0]["message"]["content"])
            return ExtractionResult(items, j.get("time_info", {}), j.get("usage", {}), j, wall_ms)
        if last is not None:
            last.raise_for_status()
        raise RuntimeError("extract_page: exhausted retries")
    finally:
        if owns:
            client.close()
