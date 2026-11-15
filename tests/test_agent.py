from datetime import date, datetime, timedelta

import pytest

from app.config import Settings
from app.models import Appointment, DoseLog, Medication, Member, Profile
from app.services.agent import run_tick
from app.services.mailer import ConsoleMailer

NOW = datetime(2026, 11, 16, 8, 0)  # naive UTC; profile timezone is UTC
SETTINGS = Settings(secret_key="x", grace_minutes=30, remind_before_minutes=15)


@pytest.fixture()
def world(db):
    profile = Profile(code="P1", elder_name="Asha", timezone="UTC")
    db.add(profile)
    db.flush()
    for tag, name, email, role in (("AAAA", "Asha", "asha@x.com", "elder"), ("BBBB", "Ravi", "ravi@x.com", "caregiver"),
                                   ("CCCC", "Meera", "meera@x.com", "family"), ("DDDD", "NoMail", None, "caregiver")):
        db.add(Member(profile_id=profile.id, tag=tag, name=name, email=email, role=role, passcode_hash="x"))
    med = Medication(profile_id=profile.id, name="Metformin", dosage="500 mg", times="08:30",
                     start_date=date(2026, 11, 1), created_at=datetime(2026, 11, 16, 7, 0))
    db.add(med)
    db.commit()
    return profile, med


def recipients(mailer, subject_has=""):
    return sorted(to for to, subject, _ in mailer.outbox if subject_has in subject)


def test_upcoming_dose_reminds_elder_once(db, world):
    mailer = ConsoleMailer()
    first = run_tick(db, mailer, SETTINGS, now=NOW + timedelta(minutes=20))  # 08:20, dose at 08:30
    assert recipients(mailer) == ["asha@x.com"] and "Time for Metformin" in mailer.outbox[0][1]
    again = run_tick(db, mailer, SETTINGS, now=NOW + timedelta(minutes=22))
    assert len(mailer.outbox) == 1 and again.emails_sent == 0 and first.emails_sent == 1


def test_no_reminder_too_early(db, world):
    mailer = ConsoleMailer()
    run_tick(db, mailer, SETTINGS, now=NOW)  # 08:00: 30 min ahead, outside the 15 min lead
    assert mailer.outbox == []


def test_missed_dose_escalates_to_caregivers_not_family(db, world):
    mailer = ConsoleMailer()
    report = run_tick(db, mailer, SETTINGS, now=NOW + timedelta(minutes=61))  # 09:01
    assert report.missed_marked == 1
    assert recipients(mailer) == ["asha@x.com", "ravi@x.com"]
    assert db.query(DoseLog).order_by(DoseLog.scheduled_at).first().status == "missed"
    run_tick(db, mailer, SETTINGS, now=NOW + timedelta(minutes=70))
    assert len(mailer.outbox) == 2  # idempotent


def test_taken_dose_triggers_nothing(db, world):
    mailer = ConsoleMailer()
    run_tick(db, mailer, SETTINGS, now=NOW + timedelta(minutes=20))
    dose = db.query(DoseLog).order_by(DoseLog.scheduled_at).first()
    dose.status = "taken"
    db.commit()
    mailer.outbox.clear()
    run_tick(db, mailer, SETTINGS, now=NOW + timedelta(minutes=70))
    assert mailer.outbox == []


def test_three_misses_alert_family(db, world):
    _, med = world
    med.times = "08:30,12:00,18:00"
    db.commit()
    mailer = ConsoleMailer()
    report = run_tick(db, mailer, SETTINGS, now=NOW + timedelta(hours=12))  # 20:00, all three slots passed
    assert report.missed_marked == 3
    concern = [(to, s) for to, s, _ in mailer.outbox if "concern" in s.lower()]
    assert sorted(to for to, _ in concern) == ["meera@x.com", "ravi@x.com"]


def test_appointment_reminders_24h_and_2h(db, world):
    profile, _ = world
    db.add(Appointment(profile_id=profile.id, title="Cardiology", doctor="Dr. Rao", starts_at=NOW + timedelta(hours=20)))
    db.commit()
    mailer = ConsoleMailer()
    run_tick(db, mailer, SETTINGS, now=NOW)
    assert recipients(mailer, "Appointment") == ["asha@x.com", "meera@x.com", "ravi@x.com"]  # everyone with an email
    mailer.outbox.clear()
    run_tick(db, mailer, SETTINGS, now=NOW + timedelta(hours=18, minutes=30))  # 90 min before
    assert recipients(mailer, "Appointment") == ["asha@x.com", "ravi@x.com"]  # 2h reminder, team only


def test_refill_alert_once_per_day(db, world):
    _, med = world
    med.pills_remaining = 2  # 2 days left at 1/day
    db.commit()
    mailer = ConsoleMailer()
    run_tick(db, mailer, SETTINGS, now=NOW)
    run_tick(db, mailer, SETTINGS, now=NOW + timedelta(hours=1))
    refills = [m for m in mailer.outbox if "Refill" in m[1]]
    assert sorted(r[0] for r in refills) == ["asha@x.com", "ravi@x.com"]


def test_failed_send_is_retried_next_tick(db, world):
    class Flaky(ConsoleMailer):
        fail = True

        def send(self, *a):
            if self.fail:
                raise OSError("smtp down")
            super().send(*a)

    mailer = Flaky()
    report = run_tick(db, mailer, SETTINGS, now=NOW + timedelta(minutes=20))
    assert report.emails_failed == 1 and not mailer.outbox
    mailer.fail = False
    run_tick(db, mailer, SETTINGS, now=NOW + timedelta(minutes=21))
    assert len(mailer.outbox) == 1
