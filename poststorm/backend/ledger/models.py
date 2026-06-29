from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (UniqueConstraint("tenant_id", "type", "key", name="uq_account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, default="demo")
    type: Mapped[str] = mapped_column(String)        # claim | provider_cash | dump_account | payer_recoup
    key: Mapped[str] = mapped_column(String)
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)


class Event(Base):
    __tablename__ = "ledger_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, default="demo")
    batch_id: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)        # payment | recoup | reversal | correction
    source_line_key: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String, default="")
    confidence: Mapped[str] = mapped_column(String, default="")
    source_span: Mapped[str] = mapped_column(String, default="")
    meta: Mapped[str] = mapped_column(String, default="{}")  # JSON string
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Entry(Base):
    __tablename__ = "ledger_entry"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("ledger_event.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    direction: Mapped[str] = mapped_column(String)   # debit | credit
    amount_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String, default="")


class PostedLine(Base):
    __tablename__ = "posted_line"
    __table_args__ = (UniqueConstraint("tenant_id", "line_key", name="uq_posted_line"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, default="demo")
    line_key: Mapped[str] = mapped_column(String)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_event.id"), nullable=True)


class ReviewException(Base):
    __tablename__ = "review_exception"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, default="demo")
    line_key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)        # ambiguous | low_confidence
    payload: Mapped[str] = mapped_column(String, default="{}")
    status: Mapped[str] = mapped_column(String, default="open")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, default="demo")
    kind: Mapped[str] = mapped_column(String)
    original_line: Mapped[str] = mapped_column(String)
    corrected_line: Mapped[str] = mapped_column(String)
    reviewer: Mapped[str] = mapped_column(String, default="demo-reviewer")
    created_at: Mapped[datetime] = mapped_column(default=_now)
