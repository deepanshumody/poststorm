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


def recoup_metrics(reconcile_result, planted_claims: set) -> dict:
    recoups = list(reconcile_result.recoups)
    matched = [r for r in recoups if r.status == "matched"]
    flagged = sum(1 for r in recoups if r.status == "needs_review" and r.recoup_claim_id in planted_claims)
    caught = sum(1 for r in matched if r.recoup_claim_id in planted_claims)
    false_positives = sum(1 for r in matched if r.recoup_claim_id not in planted_claims)
    planted = len(planted_claims)
    missed = planted - (caught + flagged)
    precision = caught / (caught + false_positives) if (caught + false_positives) else 1.0
    recall = (caught + flagged) / planted if planted else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"planted": planted, "caught": caught, "needs_review": flagged,
            "false_positives": false_positives, "missed": missed,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def confidence_calibration(extracted_by_doc, truth_by_doc, tolerance_cents: int = 0) -> dict:
    buckets = {"low": [0, 0], "high": [0, 0]}  # conf -> [errors, count]
    for doc_id, extracted in extracted_by_doc.items():
        truth = truth_by_doc.get(doc_id)
        if not truth:
            continue
        matched, _, _ = match_doc_lines(extracted, truth.get("lines", []))
        for li, tl in matched:
            conf = _extracted_val(li, "confidence")
            if conf not in buckets:
                continue  # 'medium' excluded from the low-vs-high calibration
            has_error = any(not _field_correct(li, tl, f, tolerance_cents) for f in _ALL_FIELDS)
            buckets[conf][1] += 1
            buckets[conf][0] += 1 if has_error else 0

    def _rate(b):
        return round(b[0] / b[1], 4) if b[1] else 0.0

    return {"low_count": buckets["low"][1], "high_count": buckets["high"][1],
            "low_error_rate": _rate(buckets["low"]), "high_error_rate": _rate(buckets["high"])}


def build_report(extracted_by_doc, truth_by_doc, reconcile_result, planted_claims, model, generated_at) -> dict:
    matched_n = ext_only = truth_only = 0
    for doc_id, extracted in extracted_by_doc.items():
        truth = truth_by_doc.get(doc_id)
        if not truth:
            ext_only += len(extracted)
            continue
        m, eo, to = match_doc_lines(extracted, truth.get("lines", []))
        matched_n += len(m)
        ext_only += len(eo)
        truth_only += len(to)
    return {"generated_at": generated_at, "model": model, "docs": len(extracted_by_doc),
            "field_accuracy": field_accuracy(extracted_by_doc, truth_by_doc),
            "line_match": {"matched": matched_n, "extracted_only": ext_only, "truth_only": truth_only},
            "recoup": recoup_metrics(reconcile_result, planted_claims),
            "confidence": confidence_calibration(extracted_by_doc, truth_by_doc)}
