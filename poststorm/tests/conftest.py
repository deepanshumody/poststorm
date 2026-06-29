"""Session-wide test fixtures.

Worker tests (test_ingest_worker.py) use the real file-backed SessionLocal because
process_one opens its own DB session internally.  We therefore need two things:
  1. Ensure the DB tables exist before any test runs.
  2. Wipe the hardcoded worker-test tenant rows before each test so the suite is
     idempotent across multiple pytest invocations.
"""
import pytest

from backend.ingest.models import Document, Extraction, IngestJob
from backend.ledger import db as ledger_db

# Tenant IDs whose rows are owned exclusively by test_ingest_worker.py
_WORKER_TEST_TENANTS = {"wk_a", "wk_b", "wk_c"}


@pytest.fixture(autouse=True, scope="session")
def init_real_db():
    """Create all tables in the real file-backed DB (idempotent)."""
    ledger_db.init_db()


@pytest.fixture(autouse=True)
def cleanup_worker_test_rows():
    """Delete any leftover rows for the worker test tenants so tests are idempotent."""
    s = ledger_db.SessionLocal()
    try:
        for tenant in _WORKER_TEST_TENANTS:
            s.query(Extraction).filter(Extraction.tenant_id == tenant).delete()
            s.query(Document).filter(Document.tenant_id == tenant).delete()
            s.query(IngestJob).filter(IngestJob.tenant_id == tenant).delete()
        s.commit()
    finally:
        s.close()
    yield
