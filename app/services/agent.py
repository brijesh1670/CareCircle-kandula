"""The reminder agent: a rule-driven loop that observes dose/appointment state and
decides who needs to hear what, and when (escalating from elder -> caregivers -> family).

Stages for each dose:
  1. reminder   : from `remind_before` minutes before until the grace period ends -> elder
  2. missed     : grace period elapsed, still pending -> mark missed, tell elder + caregivers
  3. concern    : 3+ missed doses in 48h -> tell caregivers + family (once a day)
Plus appointment reminders (24h to everyone, 2h to elder + caregivers) and refill alerts.
Every email is keyed in the Notification ledger, so a tick can safely run any number of times.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Appointment, DoseLog, Medication, Member, Notification, Profile, utcnow
from .insights import refill_alerts
from .mailer import Mailer
from .schedule import ensure_doses, local_today, to_local

log = logging.getLogger(__name__)
CONCERN_MISSES = 3


@dataclass
class TickReport:
    doses_created: int = 0
    missed_marked: int = 0
    emails_sent: int = 0
    emails_failed: int = 0


def _when(moment: datetime, tz: ZoneInfo) -> str:
    return to_local(moment, tz).strftime("%a %d %b, %H:%M")


class _Notifier:
    def __init__(self, db: Session, mailer: Mailer, report: TickReport, elder_name: str) -> None:
        self.db, self.mailer, self.report, self.elder_name = db, mailer, report, elder_name

    def send(self, members: list[Member], ref_key: str, subject: str, body: str) -> None:
        for m in members:
            if not m.email:
                continue
            if self.db.scalar(select(Notification.id).where(
                    Notification.ref_key == ref_key, Notification.recipient == m.email)):
                continue
            text = f"Hello {m.name},\n\n{body}\n\n- CareCircle (care for {self.elder_name})"
            try:
                self.mailer.send(m.email, subject, text)
            except Exception:  # noqa: BLE001 - never let one bad mailbox stop the loop
                log.exception("email to %s failed; will retry next tick", m.email)
                self.report.emails_failed += 1
                continue
            self.db.add(Notification(ref_key=ref_key, recipient=m.email))
            self.db.commit()
            self.report.emails_sent += 1


def run_tick(db: Session, mailer: Mailer, settings: Settings, now: datetime | None = None) -> TickReport:
    now = now or utcnow()
    report = TickReport()
    for profile in db.scalars(select(Profile)).all():
        _tick_profile(db, mailer, settings, profile, now, report)
    return report


def _tick_profile(db: Session, mailer: Mailer, settings: Settings, profile: Profile,
                  now: datetime, report: TickReport) -> None:
    members = list(db.scalars(select(Member).where(Member.profile_id == profile.id)))
    by_role = {r: [m for m in members if m.role == r] for r in ("elder", "caregiver", "family")}
    team = by_role["elder"] + by_role["caregiver"]
    tz = ZoneInfo(profile.timezone)
    notify = _Notifier(db, mailer, report, profile.elder_name)
    grace = timedelta(minutes=settings.grace_minutes)
    lead = timedelta(minutes=settings.remind_before_minutes)

    report.doses_created += ensure_doses(db, profile, now - timedelta(hours=24), now + timedelta(days=1))

    pending = db.execute(
        select(DoseLog, Medication).join(Medication, DoseLog.medication_id == Medication.id)
        .where(Medication.profile_id == profile.id, DoseLog.status == "pending",
               DoseLog.scheduled_at <= now + lead)).all()
    for dose, med in pending:
        label = f"{med.name} {med.dosage}".strip()
        at = _when(dose.scheduled_at, tz)
        if dose.scheduled_at + grace <= now:
            dose.status = "missed"
            db.commit()
            report.missed_marked += 1
            notify.send(team, f"missed:{dose.id}", f"Missed dose: {label}",
                        f"{profile.elder_name} has not confirmed the {label} dose due {at}.\n"
                        "Please check in and, if it was taken, mark it as taken in CareCircle.")
        else:
            notify.send(by_role["elder"], f"reminder:{dose.id}", f"Time for {label}",
                        f"Your {label} dose is due at {at}.\n{med.instructions}".strip())

    since = now - timedelta(hours=48)
    missed = db.scalar(select(func.count(DoseLog.id)).join(Medication, DoseLog.medication_id == Medication.id)
                       .where(Medication.profile_id == profile.id, DoseLog.status == "missed",
                              DoseLog.scheduled_at >= since)) or 0
    if missed >= CONCERN_MISSES:
        day = local_today(profile, now).isoformat()
        notify.send(by_role["caregiver"] + by_role["family"], f"concern:{profile.id}:{day}",
                    f"Adherence concern for {profile.elder_name}",
                    f"{missed} doses have been missed in the last 48 hours. A personal check-in may help.")

    for appt in db.scalars(select(Appointment).where(
            Appointment.profile_id == profile.id, Appointment.starts_at > now,
            Appointment.starts_at <= now + timedelta(hours=24))):
        text = f"{appt.title}{' with ' + appt.doctor if appt.doctor else ''} at {_when(appt.starts_at, tz)}" \
               f"{' - ' + appt.location if appt.location else ''}."
        if appt.starts_at <= now + timedelta(hours=2):
            notify.send(team, f"appt2h:{appt.id}", f"Appointment soon: {appt.title}", f"Coming up shortly: {text}")
        notify.send(members, f"appt24h:{appt.id}", f"Appointment tomorrow: {appt.title}", f"Reminder: {text}")

    meds = list(db.scalars(select(Medication).where(
        Medication.profile_id == profile.id, Medication.active.is_(True))))
    day = local_today(profile, now).isoformat()
    for alert in refill_alerts(meds):
        med = next(m for m in meds if m.name == alert["medication"])
        notify.send(team, f"refill:{med.id}:{day}", f"Refill needed: {med.name}",
                    f"About {alert['days_left']} days of {med.name} are left. Please arrange a refill.")
