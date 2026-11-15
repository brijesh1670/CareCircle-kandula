"""Dose scheduling: turns medication definitions into concrete dose rows."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DoseLog, Medication, Profile, utcnow

UTC = timezone.utc


def parse_times(csv: str) -> list[time]:
    return [time.fromisoformat(t) for t in csv.split(",") if t]


def to_utc(day: date, at: time, tz: ZoneInfo) -> datetime:
    """Local wall-clock -> naive UTC."""
    return datetime.combine(day, at, tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def to_local(moment: datetime, tz: ZoneInfo) -> datetime:
    return moment.replace(tzinfo=UTC).astimezone(tz)


def local_today(profile: Profile, now: datetime | None = None) -> date:
    return to_local(now or utcnow(), ZoneInfo(profile.timezone)).date()


def day_window(profile: Profile, now: datetime | None = None) -> tuple[datetime, datetime]:
    tz = ZoneInfo(profile.timezone)
    today = local_today(profile, now)
    return to_utc(today, time.min, tz), to_utc(today + timedelta(days=1), time.min, tz)


def ensure_doses(db: Session, profile: Profile, start: datetime, end: datetime) -> int:
    """Create any missing dose rows with start <= scheduled_at < end. Idempotent.

    Doses scheduled before the medication was added are skipped, so adding a
    medicine at 3pm never creates an instantly 'missed' 8am dose.
    """
    tz = ZoneInfo(profile.timezone)
    meds = list(db.scalars(select(Medication).where(
        Medication.profile_id == profile.id, Medication.active.is_(True))))
    if not meds:
        return 0
    existing = set(db.execute(
        select(DoseLog.medication_id, DoseLog.scheduled_at).where(
            DoseLog.medication_id.in_([m.id for m in meds]),
            DoseLog.scheduled_at >= start, DoseLog.scheduled_at < end)).all())
    first_day = to_local(start, tz).date() - timedelta(days=1)
    last_day = to_local(end, tz).date() + timedelta(days=1)
    created = 0
    for med in meds:
        slots = parse_times(med.times)
        day = max(first_day, med.start_date)
        while day <= last_day:
            if med.end_date is None or day <= med.end_date:
                for slot in slots:
                    at = to_utc(day, slot, tz)
                    if start <= at < end and at >= med.created_at and (med.id, at) not in existing:
                        db.add(DoseLog(medication_id=med.id, scheduled_at=at))
                        created += 1
            day += timedelta(days=1)
    if created:
        db.commit()
    return created


def drop_future_pending(db: Session, medication_id: int, now: datetime | None = None) -> None:
    """Remove not-yet-due pending doses (used when a schedule changes or a medicine stops)."""
    now = now or utcnow()
    for dose in db.scalars(select(DoseLog).where(
            DoseLog.medication_id == medication_id, DoseLog.status == "pending",
            DoseLog.scheduled_at > now)):
        db.delete(dose)


def resolve_dose(db: Session, dose: DoseLog, med: Medication, action: str,
                 actor: str, now: datetime | None = None) -> DoseLog:
    """Mark a dose taken/skipped. Taking decrements the pill count exactly once."""
    status = {"take": "taken", "skip": "skipped"}[action]
    if dose.status == status:
        return dose
    was_taken = dose.status == "taken"
    dose.status, dose.resolved_at, dose.resolved_by = status, now or utcnow(), actor
    if status == "taken" and med.pills_remaining is not None:
        med.pills_remaining = max(0, med.pills_remaining - med.pills_per_dose)
    elif was_taken and med.pills_remaining is not None:  # correcting a mistaken 'taken'
        med.pills_remaining += med.pills_per_dose
    db.commit()
    return dose
