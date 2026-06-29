import glob
import uuid
from dataclasses import dataclass
from pathlib import Path

from backend.config import get_settings

ALLOWED_CONTENT_TYPES = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}
_EXT_TO_TYPE = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@dataclass
class StoredBlob:
    doc_id: str
    storage_path: str
    content_type: str
    size: int


class UploadError(ValueError):
    """An invalid upload (unsupported type or too large). Carries an HTTP status_code."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _safe_name(filename: str) -> str:
    return Path(filename or "upload").name  # basename only — strips any directory / traversal


def save_upload(tenant_id: str, filename: str, data: bytes) -> StoredBlob:
    s = get_settings()
    name = _safe_name(filename)
    ext = Path(name).suffix.lower()
    content_type = _EXT_TO_TYPE.get(ext)
    if content_type is None:
        raise UploadError(f"unsupported file type: {ext or '(none)'}", 415)
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise UploadError("file too large", 413)
    doc_id = "d_" + uuid.uuid4().hex[:12]
    tenant_dir = Path(s.upload_dir) / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    path = tenant_dir / f"{doc_id}{ALLOWED_CONTENT_TYPES[content_type]}"
    path.write_bytes(data)
    return StoredBlob(doc_id=doc_id, storage_path=str(path), content_type=content_type, size=len(data))


def fixture_paths(count: int) -> list[str]:
    eobs = Path(__file__).resolve().parents[2] / "data" / "eobs"
    pngs = [p for p in sorted(glob.glob(str(eobs / "*.png"))) if ".thumb." not in Path(p).name]
    return pngs[:count]
