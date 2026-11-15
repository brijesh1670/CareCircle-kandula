from datetime import date, datetime, timedelta

from app.models import DoseLog, Medication, Profile
from app.services import interactions
from app.services.insights import build_insights, refill_alerts
from app.services.schedule import ensure_doses, resolve_dose

NOW = datetime(2026, 11, 16, 12, 0)  # naive UTC


def _setup(db, **med_kw):
    profile = Profile(code="P1", elder_name="Asha", timezone="UTC")
    db.add(profile)
    db.flush()
    med = Medication(profile_id=profile.id, name="Metformin", dosage="500 mg", times="08:00,20:00",
                     start_date=date(2026, 11, 1), created_at=datetime(2026, 11, 1), **med_kw)
    db.add(med)
    db.commit()
    return profile, med


def test_ensure_doses_is_idempotent(db):
    profile, _ = _setup(db)
    start, end = NOW - timedelta(days=1), NOW + timedelta(days=1)
    assert ensure_doses(db, profile, start, end) == 4
    assert ensure_doses(db, profile, start, end) == 0


def test_no_doses_before_medication_was_added(db):
    profile, med = _setup(db)
    med.created_at = datetime(2026, 11, 16, 9, 0)  # added after the 08:00 slot
    db.commit()
    ensure_doses(db, profile, datetime(2026, 11, 16), datetime(2026, 11, 17))
    times = [d.scheduled_at.hour for d in db.query(DoseLog)]
    assert times == [20]


def test_respects_timezone(db):
    profile, _ = _setup(db)
    profile.timezone = "Asia/Kolkata"  # UTC+5:30
    db.commit()
    ensure_doses(db, profile, datetime(2026, 11, 16), datetime(2026, 11, 17))
    assert sorted(d.scheduled_at.strftime("%H:%M") for d in db.query(DoseLog)) == ["02:30", "14:30"]


def test_taking_decrements_pills_once_and_can_be_corrected(db):
    profile, med = _setup(db, pills_remaining=10, pills_per_dose=2)
    ensure_doses(db, profile, NOW - timedelta(days=1), NOW)
    dose = db.query(DoseLog).first()
    resolve_dose(db, dose, med, "take", "Asha")
    resolve_dose(db, dose, med, "take", "Asha")  # double tap
    assert med.pills_remaining == 8
    resolve_dose(db, dose, med, "skip", "Asha")  # corrected
    assert med.pills_remaining == 10 and dose.status == "skipped"


def test_adherence_streak_and_weak_slot(db):
    profile, _ = _setup(db)
    ensure_doses(db, profile, datetime(2026, 11, 9), NOW)
    for dose in db.query(DoseLog):
        dose.status = "missed" if dose.scheduled_at.hour == 20 else "taken"
    db.commit()
    result = build_insights(db, profile, NOW)
    assert result["adherence_7d"] == 50 and result["risk"] == "at_risk"
    assert result["weakest_slot"]["time"] == "20:00"
    assert result["streak_days"] == 0


def test_perfect_adherence_builds_streak(db):
    profile, _ = _setup(db)
    ensure_doses(db, profile, datetime(2026, 11, 9), NOW)
    for dose in db.query(DoseLog):
        dose.status = "taken"
    db.commit()
    result = build_insights(db, profile, NOW)
    assert result["adherence_7d"] == 100 and result["risk"] == "good"
    assert result["streak_days"] == 7 and result["weakest_slot"] is None


def test_refill_alert(db):
    _, med = _setup(db, pills_remaining=5)  # 2/day -> 2.5 days
    assert refill_alerts([med]) == [{"medication": "Metformin", "days_left": 2.5}]
    med.pills_remaining = 60
    assert refill_alerts([med]) == []


def test_interaction_checks():
    assert interactions.check_new_medication("Aspirin 75mg", ["Warfarin"])
    assert "duplicate" in interactions.check_new_medication("metformin", ["Metformin"])[0]
    assert interactions.check_new_medication("Metformin", ["Warfarin"]) == []
