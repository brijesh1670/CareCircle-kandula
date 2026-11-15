"""Adherence analytics: rates, streaks, weak time-slots, refill and risk signals."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DoseLog, Medication, Profile
from . import interactions
from .schedule import local_today, parse_times, to_local

REFILL_WARN_DAYS = 3


def _rate(statuses: list[str]) -> float | None:
    return round(100 * statuses.count("taken") / len(statuses)) if statuses else None


def _risk(rate: float | None) -> str:
    if rate is None:
        return "unknown"
    return "good" if rate >= 90 else "watch" if rate >= 75 else "at_risk"


def refill_alerts(meds: list[Medication]) -> list[dict]:
    alerts = []
    for med in meds:
        if med.pills_remaining is None:
            continue
        per_day = med.pills_per_dose * max(1, len(parse_times(med.times)))
        days_left = med.pills_remaining / per_day
        if days_left <= REFILL_WARN_DAYS:
            alerts.append({"medication": med.name, "days_left": round(days_left, 1)})
    return alerts


def build_insights(db: Session, profile: Profile, now: datetime) -> dict:
    tz = ZoneInfo(profile.timezone)
    meds = list(db.scalars(select(Medication).where(
        Medication.profile_id == profile.id, Medication.active.is_(True))))
    names = {m.id: m.name for m in meds}
    rows = db.execute(select(DoseLog.medication_id, DoseLog.scheduled_at, DoseLog.status).where(
        DoseLog.medication_id.in_(names), DoseLog.scheduled_at >= now - timedelta(days=30),
        DoseLog.scheduled_at <= now, DoseLog.status != "pending")).all() if names else []

    last7 = [s for _, at, s in rows if at >= now - timedelta(days=7)]
    by_med: dict[str, list[str]] = defaultdict(list)
    by_slot: dict[str, list[str]] = defaultdict(list)
    by_day: dict = defaultdict(list)
    for med_id, at, status in rows:
        local = to_local(at, tz)
        if at >= now - timedelta(days=7):
            by_med[names[med_id]].append(status)
        by_slot[local.strftime("%H:%M")].append(status)
        by_day[local.date()].append(status)

    streak, day = 0, local_today(profile, now) - timedelta(days=1)
    while by_day.get(day) and all(s == "taken" for s in by_day[day]):
        streak, day = streak + 1, day - timedelta(days=1)

    slots = {t: _rate(s) for t, s in by_slot.items() if len(s) >= 3}
    weakest = min(slots, key=slots.get) if slots else None
    weak = {"time": weakest, "rate": slots[weakest]} if weakest and slots[weakest] < 90 else None

    rate7 = _rate(last7)
    return {
        "adherence_7d": rate7,
        "adherence_30d": _rate([s for _, _, s in rows]),
        "risk": _risk(rate7),
        "streak_days": streak,
        "weakest_slot": weak,
        "by_medication": [{"medication": n, "rate": _rate(s)} for n, s in sorted(by_med.items())],
        "refill_alerts": refill_alerts(meds),
        "interaction_notes": interactions.scan_all([m.name for m in meds]),
    }
