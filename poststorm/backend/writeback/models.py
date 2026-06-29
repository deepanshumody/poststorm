from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.ledger.models import Base, _now


class Delivery(Base):
    __tablename__ = "delivery"
    __table_args__ = (UniqueConstraint("tenant_id", "event_id", "destination", name="uq_delivery"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    event_id: Mapped[int] = mapped_column(ForeignKey("ledger_event.id"))
    destination: Mapped[str] = mapped_column(String)        # file | webhook
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|delivering|delivered|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String)
    payload_sha256: Mapped[str] = mapped_column(String, default="")
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
