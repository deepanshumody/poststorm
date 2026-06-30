from backend.ledger.money import to_cents

_STRING_FIELDS = ("claim_id", "payer", "patient_ref", "carc")
_MONEY_FIELDS = ("paid", "charge", "allowed", "adjustment")
_CATEGORICAL_FIELDS = ("recoup_flag", "event_type")
_ALL_FIELDS = _STRING_FIELDS + _MONEY_FIELDS + _CATEGORICAL_FIELDS


def _extracted_val(li, field):
    v = getattr(li, field, None)
    return v.value if hasattr(v, "value") else v   # EventType/Confidence enums -> their string


def _field_correct(li, truth, field, tolerance_cents) -> bool:
    ev = _extracted_val(li, field)
    tv = truth.get(field)
    if field in _MONEY_FIELDS:
        return abs(to_cents(ev or 0) - to_cents(tv or 0)) <= tolerance_cents
    return ev == tv


def match_doc_lines(extracted, truth_lines):
    truth_by_claim = {t["claim_id"]: t for t in truth_lines}
    ext_by_claim = {li.claim_id: li for li in extracted}
    matched = [(ext_by_claim[c], truth_by_claim[c]) for c in ext_by_claim if c in truth_by_claim]
    extracted_only = [c for c in ext_by_claim if c not in truth_by_claim]
    truth_only = [c for c in truth_by_claim if c not in ext_by_claim]
    return matched, extracted_only, truth_only


def field_accuracy(extracted_by_doc, truth_by_doc, tolerance_cents: int = 0) -> dict:
    correct = {f: 0 for f in _ALL_FIELDS}
    total = {f: 0 for f in _ALL_FIELDS}
    for doc_id, extracted in extracted_by_doc.items():
        truth = truth_by_doc.get(doc_id)
        if not truth:
            continue
        matched, _, _ = match_doc_lines(extracted, truth.get("lines", []))
        for li, tl in matched:
            for f in _ALL_FIELDS:
                total[f] += 1
                if _field_correct(li, tl, f, tolerance_cents):
                    correct[f] += 1
    by_field = {f: (correct[f] / total[f] if total[f] else 1.0) for f in _ALL_FIELDS}
    cells = sum(total.values())
    overall = sum(correct.values()) / cells if cells else 1.0
    return {"overall": overall, "by_field": by_field}
