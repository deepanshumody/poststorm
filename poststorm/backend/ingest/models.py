from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.ledger.models import Base, _now


class IngestJob(Base):
    __tablename__ = "ingest_job"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    doc_count: Mapped[int] = mapped_column(Integer, default=0)
    post_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    finalized_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Document(Base):
    __tablename__ = "document"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    job_id: Mapped[str] = mapped_column(ForeignKey("ingest_job.id"))
    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String)
    storage_path: Mapped[str] = mapped_column(String)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)


class Extraction(Base):
    __tablename__ = "extraction"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("document.id"))
    tenant_id: Mapped[str] = mapped_column(String)
    line_items_json: Mapped[str] = mapped_column(String, default="[]")
    model: Mapped[str] = mapped_column(String, default="")
    usage_json: Mapped[str] = mapped_column(String, default="{}")
    wall_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=_now)
