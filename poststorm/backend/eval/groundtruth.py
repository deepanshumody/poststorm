import json
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parents[2] / "data" / "ground_truth.json"


def load(path: str | None = None) -> dict:
    p = Path(path) if path else _DEFAULT
    return {d["doc_id"]: d for d in json.loads(p.read_text())}


def planted_recoup_claims(gt: dict) -> set:
    claims: set = set()
    for doc in gt.values():
        if not doc.get("has_planted_recoup"):
            continue
        for ln in doc.get("lines", []):
            if ln.get("recoup_flag") or ln.get("event_type") == "recoup" or (ln.get("paid") or 0) < 0:
                claims.add(ln["claim_id"])
    return claims
