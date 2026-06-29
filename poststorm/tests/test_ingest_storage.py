import pytest

from backend.config import get_settings
from backend.ingest import storage


def test_save_upload_writes_file_and_returns_blob(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    blob = storage.save_upload("demo", "scan.png", b"PNGDATA")
    assert blob.doc_id.startswith("d_") and blob.content_type == "image/png"
    from pathlib import Path
    assert Path(blob.storage_path).read_bytes() == b"PNGDATA"
    assert str(tmp_path) in blob.storage_path and "/demo/" in blob.storage_path


def test_unsupported_type_raises_415(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    with pytest.raises(storage.UploadError) as e:
        storage.save_upload("demo", "notes.txt", b"x")
    assert e.value.status_code == 415


def test_oversize_raises_413(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    with pytest.raises(storage.UploadError) as e:
        storage.save_upload("demo", "big.png", b"x" * (1024 * 1024 + 1))
    assert e.value.status_code == 413


def test_filename_traversal_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    blob = storage.save_upload("demo", "../../etc/evil.png", b"x")
    from pathlib import Path
    # the file lands under the tenant dir, never outside it
    assert Path(blob.storage_path).resolve().is_relative_to((tmp_path / "demo").resolve())


def test_fixture_paths_returns_corpus_pngs():
    paths = storage.fixture_paths(3)
    assert 1 <= len(paths) <= 3
    assert all(p.endswith(".png") and ".thumb." not in p for p in paths)
