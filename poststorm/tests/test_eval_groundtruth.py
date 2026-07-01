from backend.eval import groundtruth


def test_load_indexes_by_doc_id():
    gt = groundtruth.load()
    assert len(gt) == 24
    any_doc = next(iter(gt.values()))
    assert "lines" in any_doc and "doc_id" in any_doc


def test_planted_recoup_claims_finds_four():
    gt = groundtruth.load()
    planted = groundtruth.planted_recoup_claims(gt)
    assert len(planted) == 4 and all(isinstance(c, str) for c in planted)
