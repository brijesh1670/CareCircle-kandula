"""Database models. Only portable SQLAlchemy types are used so the same schema
is intended to run on SQLite (default) and IBM Db2 (via ibm_db_sa)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Integer, String,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("elder", "caregiver", "family")
WRITE_ROLES = ("elder", "caregiver")


def utcnow() -> datetime:
    """Naive UTC 'now'. All datetimes are stored as naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    elder_name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("profile_id", "tag"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    tag: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    role: Mapped[str] = mapped_column(String(10))
    passcode_hash: Mapped[str] = mapped_column(String(200))
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Medication(Base):
    __tablename__ = "medications"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    dosage: Mapped[str] = mapped_column(String(60), default="")
    instructions: Mapped[str] = mapped_column(String(300), default="")
    times: Mapped[str] = mapped_column(String(80))  # "08:00,20:00" in the profile's timezone
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pills_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pills_per_dose: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DoseLog(Base):
    __tablename__ = "dose_logs"
    __table_args__ = (UniqueConstraint("medication_id", "scheduled_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medications.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|taken|skipped|missed
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    doctor: Mapped[str] = mapped_column(String(100), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(String(500), default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Notification(Base):
    """Ledger of sent emails; the unique key makes reminders idempotent."""
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("ref_key", "recipient"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ref_key: Mapped[str] = mapped_column(String(120))
    recipient: Mapped[str] = mapped_column(String(254))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    actor_role: Mapped[str] = mapped_column(String(10))
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(String(300), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
