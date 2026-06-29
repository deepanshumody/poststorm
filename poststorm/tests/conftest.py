import pytest
from sqlalchemy import text

from backend.ledger import db as ledger_db


@pytest.fixture(autouse=True)
def isolate_review_tables(request):
    """Before each test in test_review_api, clear review exceptions and their
    placeholder PostedLine rows so _seed() can create fresh exceptions."""
    if "test_review_api" not in request.module.__name__:
        yield
        return
    engine = ledger_db._init_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM review_exception"))
        conn.execute(text("DELETE FROM posted_line WHERE event_id IS NULL"))
        conn.commit()
    yield
