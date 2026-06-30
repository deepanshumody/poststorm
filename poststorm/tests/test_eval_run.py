from backend import extract, images
from backend.config import get_settings
from backend.eval import groundtruth, run
from backend.extract import ExtractionResult
from backend.schema import LineItem


def _lines_for(doc):
    return [LineItem(**ln) for ln in doc["lines"]]


def test_run_eval_perfect_when_extractor_returns_truth(monkeypatch):
    gt = groundtruth.load()
    doc_id = next(iter(gt))
    paths = [f"/x/{doc_id}.png"]
    monkeypatch.setattr(images, "load_page_images", lambda p: ["img"])
    monkeypatch.setattr(images, "image_to_data_uri", lambda img, **kw: "data:image/png;base64,AA==")
    monkeypatch.setattr(extract, "extract_page",
                        lambda uri, **kw: ExtractionResult(_lines_for(gt[doc_id]), {}, {}, {}, 1.0))
    report = run.run_eval(paths, gt, "gemma-4-31b")
    assert report["docs"] == 1 and report["field_accuracy"]["overall"] == 1.0


def test_write_then_read_report_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_dir", str(tmp_path))
    assert run.read_report() is None
    run.write_report({"docs": 3, "model": "m"})
    assert run.read_report()["docs"] == 3


def test_run_eval_skips_a_failing_doc(monkeypatch):
    gt = groundtruth.load()
    doc_id = next(iter(gt))
    monkeypatch.setattr(images, "load_page_images", lambda p: ["img"])
    monkeypatch.setattr(images, "image_to_data_uri", lambda img, **kw: "x")

    def boom(uri, **kw):
        raise RuntimeError("internal://secret-host/boom")

    monkeypatch.setattr(extract, "extract_page", boom)
    report = run.run_eval([f"/x/{doc_id}.png"], gt, "m")
    assert report["docs"] == 0  # the failing doc was skipped, not crashed
