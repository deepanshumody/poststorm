"""Live evaluation: run Gemma-4 extraction over the synthetic corpus and score it
against ground truth — field accuracy + the headline recoupment precision/recall.

Run from poststorm/:  python -m eval.evaluate
Writes eval/report.json and prints a markdown summary.
"""
import json
import time
from pathlib import Path

from backend import extract, images, reconcile

ROOT = Path(__file__).resolve().parents[1]
GT = ROOT / "data" / "ground_truth.json"
OUT = Path(__file__).resolve().parent / "report.json"


def _last(name: str) -> str:
    name = (name or "").strip()
    head = name.split(",")[0] if "," in name else name.split()[-1] if name.split() else ""
    return head.strip().upper()


def _match_lines(gt_lines, ex_lines):
    """Greedily pair each ground-truth line to the extracted line with the closest
    |paid| (claim_ids are unreliable through OCR, amounts are the stable key)."""
    pool = list(ex_lines)
    for gt in gt_lines:
        best, bestd = None, 1e9
        for ex in pool:
            d = abs(gt["paid"] - ex.paid)
            if d < bestd:
                best, bestd = ex, d
        yield gt, (best if bestd < 0.01 else None)
        if best is not None and bestd < 0.01:
            pool.remove(best)


def main():
    truth = json.loads(GT.read_text())
    n_lines = paid_ok = name_ok = check_ok = flag_ok = lines_extracted = 0
    tp = fp = planted = 0  # recoup detection
    per_doc = []

    t0 = time.perf_counter()
    for doc in truth:
        png = ROOT / "data" / "eobs" / f"{doc['doc_id']}.png"
        res = extract.extract_page(images.image_to_data_uri(images.load_page_images(str(png))[0], max_dim=1600))
        lines_extracted += len(res.line_items)

        for gt, ex in _match_lines(doc["lines"], res.line_items):
            n_lines += 1
            if ex is None:
                continue
            paid_ok += 1  # matched within $0.01 by construction
            name_ok += _last(gt["patient_ref"]) == _last(ex.patient_ref)
            check_ok += gt["check_number"] == ex.check_number
            flag_ok += bool(gt["recoup_flag"]) == bool(ex.recoup_flag)

        rr = reconcile.reconcile(res.line_items)
        caught = [x for x in rr.recoups if x.status == "matched" and x.cross_patient]
        if doc["has_planted_recoup"]:
            planted += 1
            tp += 1 if len(caught) == 1 else 0
        else:
            fp += len(caught)  # any cross-patient recoup on a clean doc is a false positive
        per_doc.append({"doc_id": doc["doc_id"], "template": doc.get("template"),
                        "planted": doc["has_planted_recoup"], "caught": len(caught)})

    dur = time.perf_counter() - t0
    pct = lambda a, b: round(100 * a / b, 1) if b else 0.0  # noqa: E731
    report = {
        "docs": len(truth),
        "lines_expected": n_lines,
        "lines_extracted": lines_extracted,
        "field_accuracy_pct": {
            "paid_amount": pct(paid_ok, n_lines),
            "patient_name": pct(name_ok, n_lines),
            "check_number": pct(check_ok, n_lines),
            "recoup_flag": pct(flag_ok, n_lines),
        },
        "recoupment_detection": {
            "planted": planted, "true_positives": tp, "false_positives": fp,
            "recall_pct": pct(tp, planted), "precision_pct": pct(tp, tp + fp),
        },
        "wall_seconds": round(dur, 1),
        "per_doc": per_doc,
    }
    OUT.write_text(json.dumps(report, indent=2))

    fa = report["field_accuracy_pct"]
    rd = report["recoupment_detection"]
    print(f"\n## Evaluation ({report['docs']} synthetic EOBs, live Gemma-4 on Cerebras, {report['wall_seconds']}s)\n")
    print("| Metric | Result |")
    print("|---|---|")
    print(f"| Lines extracted / expected | {lines_extracted} / {n_lines} |")
    print(f"| Paid-amount accuracy | {fa['paid_amount']}% |")
    print(f"| Patient-name accuracy | {fa['patient_name']}% |")
    print(f"| Check-number accuracy | {fa['check_number']}% |")
    print(f"| Recoup-flag accuracy | {fa['recoup_flag']}% |")
    print(f"| **Recoupment recall** | **{rd['recall_pct']}%** ({rd['true_positives']}/{rd['planted']}) |")
    print(f"| **Recoupment precision** | **{rd['precision_pct']}%** ({rd['false_positives']} false positives) |")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
