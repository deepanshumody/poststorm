from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings
from backend.ledger.models import Base

_engine = None
_Session = None


def _init_engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, future=True)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragmas(dbapi_conn, _rec):  # noqa: ARG001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA busy_timeout=5000")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    Base.metadata.create_all(_init_engine())


def SessionLocal() -> Session:
    _init_engine()
    return _Session()


def make_memory_session() -> Session:
    """In-memory engine with tables — for tests."""
    eng = create_engine("sqlite://", future=True)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()
