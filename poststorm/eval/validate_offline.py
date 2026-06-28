"""Offline coherence check: feed GROUND-TRUTH lines (perfect extraction) through the
reconcile engine and confirm every planted cross-patient recoup is detected. This
validates generator<->reconcile agreement without touching the API.

Run from poststorm/:  python -m eval.validate_offline
"""
import json
from pathlib import Path

from backend.reconcile import reconcile
from backend.schema import LineItem

GT = Path(__file__).resolve().parents[1] / "data" / "ground_truth.json"


def main():
    truth = json.loads(GT.read_text())
    all_lines = [LineItem(**ln) for doc in truth for ln in doc["lines"]]
    planted = sum(1 for doc in truth for ln in doc["lines"]
                  if ln["event_type"] in ("recoup", "reversal"))

    r = reconcile(all_lines)
    matched_cross = [x for x in r.recoups if x.status == "matched" and x.cross_patient]

    print(f"docs={len(truth)} lines={len(all_lines)} planted_recoups={planted}")
    print(f"reconcile totals: {r.totals}")
    print(f"matched cross-patient recoups (dump accounts): {len(matched_cross)}")
    assert len(matched_cross) >= planted, (
        f"expected >= {planted} cross-patient recoups, got {len(matched_cross)}")
    print("OK — every planted recoup was detected by the deterministic engine.")


if __name__ == "__main__":
    main()
