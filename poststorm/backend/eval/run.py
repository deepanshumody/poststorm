import glob
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from backend import extract, images
from backend.config import get_settings
from backend.eval import groundtruth, score
from backend.logging_config import get_logger
from backend.reconcile import reconcile

log = get_logger("poststorm.eval")


def _stem(p: str) -> str:
    return Path(p).stem


def run_eval(paths: list[str], gt: dict, model: str) -> dict:
    extracted_by_doc: dict = {}
    all_lines: list = []
    for p in paths:
        try:
            uri = images.image_to_data_uri(images.load_page_images(p)[0], max_dim=1600)
            res = extract.extract_page(uri)
        except Exception:
            log.exception("eval extraction failed for %s", p)  # full trace server-side only
            continue
        extracted_by_doc[_stem(p)] = res.line_items
        all_lines.extend(res.line_items)
    rr = reconcile(all_lines)
    planted = groundtruth.planted_recoup_claims(gt)
    return score.build_report(extracted_by_doc, gt, rr, planted, model, datetime.now(UTC).isoformat())


def report_path(settings=None) -> Path:
    s = settings or get_settings()
    return Path(s.eval_dir) / "report.json"


def write_report(report: dict, settings=None) -> str:
    p = report_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2))
    os.replace(tmp, p)
    return str(p)


def read_report(settings=None) -> dict | None:
    p = report_path(settings)
    return json.loads(p.read_text()) if p.exists() else None


def _corpus_paths() -> list[str]:
    eobs = Path(__file__).resolve().parents[2] / "data" / "eobs"
    return [x for x in sorted(glob.glob(str(eobs / "*.png"))) if ".thumb." not in Path(x).name]


def main() -> None:
    s = get_settings()
    report = run_eval(_corpus_paths(), groundtruth.load(), s.cerebras_model)
    path = write_report(report)
    print(f"wrote {path}")
    print(json.dumps({k: report[k] for k in ("field_accuracy", "line_match", "recoup", "confidence")}, indent=2))


if __name__ == "__main__":
    main()
