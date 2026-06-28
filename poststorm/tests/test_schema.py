import json

from backend import schema


def test_response_schema_is_strict_object():
    s = schema.RESPONSE_SCHEMA
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert "line_items" in s["properties"]


def test_parse_line_items():
    payload = json.dumps({"line_items": [{
        "claim_id": "C1", "payer": "Aetna", "patient_ref": "P-A",
        "service_date": "2026-01-04", "carc": "CO-45", "rarc": [],
        "charge": 200.0, "allowed": 120.0, "paid": 120.0, "adjustment": 80.0,
        "patient_responsibility": 0.0, "event_type": "payment", "recoup_flag": False,
        "offset_link": None, "check_number": "CHK99", "confidence": "high",
        "source_span": "page 1: C1 Aetna 120.00"}]})
    items = schema.parse_line_items(payload)
    assert len(items) == 1 and items[0].event_type == schema.EventType.payment
