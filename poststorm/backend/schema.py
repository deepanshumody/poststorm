import json
from enum import Enum

from pydantic import BaseModel


class EventType(str, Enum):
    payment = "payment"
    adjustment = "adjustment"
    recoup = "recoup"
    reversal = "reversal"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class LineItem(BaseModel):
    claim_id: str
    payer: str
    patient_ref: str
    service_date: str
    carc: str | None = None
    rarc: list[str] = []
    charge: float
    allowed: float
    paid: float
    adjustment: float
    patient_responsibility: float
    event_type: EventType
    recoup_flag: bool
    offset_link: str | None = None
    check_number: str
    confidence: Confidence
    source_span: str


_LINE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim_id": {"type": "string"},
        "payer": {"type": "string"},
        "patient_ref": {"type": "string"},
        "service_date": {"type": "string"},
        "carc": {"type": ["string", "null"]},
        "rarc": {"type": "array", "items": {"type": "string"}},
        "charge": {"type": "number"},
        "allowed": {"type": "number"},
        "paid": {"type": "number"},
        "adjustment": {"type": "number"},
        "patient_responsibility": {"type": "number"},
        "event_type": {"type": "string", "enum": ["payment", "adjustment", "recoup", "reversal"]},
        "recoup_flag": {"type": "boolean"},
        "offset_link": {"type": ["string", "null"]},
        "check_number": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "source_span": {"type": "string"},
    },
    "required": [
        "claim_id", "payer", "patient_ref", "service_date", "carc", "rarc", "charge",
        "allowed", "paid", "adjustment", "patient_responsibility", "event_type",
        "recoup_flag", "offset_link", "check_number", "confidence", "source_span",
    ],
}

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"line_items": {"type": "array", "items": _LINE}},
    "required": ["line_items"],
}


def parse_line_items(content: str) -> list[LineItem]:
    data = json.loads(content)
    return [LineItem(**li) for li in data["line_items"]]
