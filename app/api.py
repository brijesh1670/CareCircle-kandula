"""HTTP API. Role model:
   elder     - full control, manages the circle, sees the activity log
   caregiver - manages medicines/appointments, records doses
   family    - read-only visibility
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import schemas
from .deps import audit, current_member, get_db, profile_of, require
from .models import (WRITE_ROLES, Appointment, AuditLog, DoseLog, Medication, Member,
                     Profile, utcnow)
from .security import (DUMMY_HASH, hash_passcode, issue_token, new_passcode,
                       new_profile_code, new_tag, split_passcode, verify_passcode)
from .services import interactions
from .services.agent import run_tick
from .services.insights import build_insights
from .services.schedule import (day_window, drop_future_pending, ensure_doses,
                                local_today, resolve_dose, to_local, to_utc)

router = APIRouter(prefix="/api")
WRITERS = require(*WRITE_ROLES)
ELDER = require("elder")


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") + "Z" if moment else None


def _new_member(db: Session, profile: Profile, name: str, email: str | None, role: str) -> tuple[Member, str]:
    taken = set(db.scalars(select(Member.tag).where(Member.profile_id == profile.id)))
    tag = new_tag()
    while tag in taken:
        tag = new_tag()
    passcode = new_passcode(tag)
    member = Member(profile_id=profile.id, tag=tag, name=name, email=email, role=role,
                    passcode_hash=hash_passcode(passcode))
    db.add(member)
    return member, passcode


def _owned(db: Session, model, item_id: int, member: Member):
    item = db.get(model, item_id)
    if item is None or item.profile_id != member.profile_id:
        raise HTTPException(404, "Not found")
    return item


def _med_out(med: Medication) -> dict:
    return {"id": med.id, "name": med.name, "dosage": med.dosage, "instructions": med.instructions,
            "times": med.times.split(","), "start_date": med.start_date.isoformat(),
            "end_date": med.end_date.isoformat() if med.end_date else None,
            "pills_remaining": med.pills_remaining, "pills_per_dose": med.pills_per_dose}


# ---------- onboarding & sign-in ----------

@router.post("/profiles", status_code=201)
def create_profile(body: schemas.ProfileCreate, db: Session = Depends(get_db)):
    code = new_profile_code()
    while db.scalar(select(Profile.id).where(Profile.code == code)):
        code = new_profile_code()
    profile = Profile(code=code, elder_name=body.elder_name, timezone=body.timezone)
    db.add(profile)
    db.flush()
    elder, passcode = _new_member(db, profile, body.elder_name, body.elder_email, "elder")
    db.flush()
    audit(db, elder, "profile_created")
    db.commit()
    return {"profile_code": code, "passcode": passcode,
            "notice": "Save this passcode now. It is shown only once."}


@router.post("/auth/login")
def login(body: schemas.LoginIn, request: Request, db: Session = Depends(get_db)):
    state = request.app.state
    key = f"{body.profile_code.upper()}|{request.client.host if request.client else '?'}"
    wait = state.throttle.retry_after(key)
    if wait:
        raise HTTPException(429, f"Too many attempts. Try again in {wait} seconds.",
                            headers={"Retry-After": str(wait)})
    member = None
    parts = split_passcode(body.passcode)
    profile = db.scalar(select(Profile).where(Profile.code == body.profile_code.upper()))
    if parts and profile:
        member = db.scalar(select(Member).where(Member.profile_id == profile.id, Member.tag == parts[0]))
    valid = verify_passcode(body.passcode.strip().upper(), member.passcode_hash if member else DUMMY_HASH)
    if not (valid and member):
        state.throttle.failure(key)
        raise HTTPException(401, "Profile code or passcode is not correct")
    state.throttle.success(key)
    settings = state.settings
    token = issue_token(settings.secret_key, member.id, member.profile_id, member.token_version,
                        settings.token_ttl_minutes)
    return {"token": token, "role": member.role, "name": member.name}


@router.get("/me")
def me(member: Member = Depends(current_member), db: Session = Depends(get_db)):
    profile = profile_of(member, db)
    return {"name": member.name, "role": member.role, "elder_name": profile.elder_name,
            "timezone": profile.timezone, "profile_code": profile.code}


# ---------- circle (members) ----------

@router.get("/members")
def list_members(member: Member = Depends(current_member), db: Session = Depends(get_db)):
    rows = db.scalars(select(Member).where(Member.profile_id == member.profile_id).order_by(Member.id))
    return [{"id": m.id, "name": m.name, "email": m.email if member.role != "family" else None,
             "role": m.role} for m in rows]


@router.post("/members", status_code=201)
def add_member(body: schemas.MemberCreate, member: Member = Depends(ELDER), db: Session = Depends(get_db)):
    profile = profile_of(member, db)
    new, passcode = _new_member(db, profile, body.name, body.email, body.role)
    db.flush()
    audit(db, member, "member_added", f"{body.name} ({body.role})")
    db.commit()
    return {"id": new.id, "name": new.name, "role": new.role, "passcode": passcode,
            "notice": "Share this passcode privately. It is shown only once."}


@router.post("/members/{member_id}/rotate")
def rotate_passcode(member_id: int, member: Member = Depends(ELDER), db: Session = Depends(get_db)):
    target = _owned(db, Member, member_id, member)
    passcode = new_passcode(target.tag)
    target.passcode_hash = hash_passcode(passcode)
    target.token_version += 1
    audit(db, member, "passcode_rotated", target.name)
    db.commit()
    return {"id": target.id, "passcode": passcode, "notice": "Old sessions were signed out."}


@router.delete("/members/{member_id}", status_code=204)
def remove_member(member_id: int, member: Member = Depends(ELDER), db: Session = Depends(get_db)):
    target = _owned(db, Member, member_id, member)
    if target.role == "elder":
        raise HTTPException(400, "The profile owner cannot be removed")
    audit(db, member, "member_removed", target.name)
    db.delete(target)
    db.commit()


# ---------- medications ----------

@router.get("/medications")
def list_medications(member: Member = Depends(current_member), db: Session = Depends(get_db)):
    meds = db.scalars(select(Medication).where(
        Medication.profile_id == member.profile_id, Medication.active.is_(True)).order_by(Medication.name))
    return [_med_out(m) for m in meds]


@router.post("/medications", status_code=201)
def add_medication(body: schemas.MedicationIn, member: Member = Depends(WRITERS), db: Session = Depends(get_db)):
    profile = profile_of(member, db)
    existing = list(db.scalars(select(Medication.name).where(
        Medication.profile_id == profile.id, Medication.active.is_(True))))
    med = Medication(profile_id=profile.id, name=body.name, dosage=body.dosage,
                     instructions=body.instructions, times=",".join(body.times),
                     start_date=body.start_date or local_today(profile), end_date=body.end_date,
                     pills_remaining=body.pills_remaining, pills_per_dose=body.pills_per_dose)
    db.add(med)
    db.flush()
    audit(db, member, "medication_added", f"{med.name} {med.dosage} at {med.times}")
    db.commit()
    return {**_med_out(med), "warnings": interactions.check_new_medication(med.name, existing)}


@router.patch("/medications/{med_id}")
def update_medication(med_id: int, body: schemas.MedicationUpdate,
                      member: Member = Depends(WRITERS), db: Session = Depends(get_db)):
    med = _owned(db, Medication, med_id, member)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(med, field, ",".join(value) if field == "times" else value)
    if {"times", "end_date"} & changes.keys():
        drop_future_pending(db, med.id)
    audit(db, member, "medication_updated", f"{med.name}: {', '.join(changes)}")
    db.commit()
    return _med_out(med)


@router.delete("/medications/{med_id}", status_code=204)
def stop_medication(med_id: int, member: Member = Depends(WRITERS), db: Session = Depends(get_db)):
    med = _owned(db, Medication, med_id, member)
    med.active = False
    drop_future_pending(db, med.id)
    audit(db, member, "medication_stopped", med.name)
    db.commit()


# ---------- doses ----------

@router.get("/doses/today")
def doses_today(member: Member = Depends(current_member), db: Session = Depends(get_db)):
    profile = profile_of(member, db)
    start, end = day_window(profile)
    ensure_doses(db, profile, start, end)
    tz = ZoneInfo(profile.timezone)
    rows = db.execute(select(DoseLog, Medication).join(Medication, DoseLog.medication_id == Medication.id)
                      .where(Medication.profile_id == profile.id, DoseLog.scheduled_at >= start,
                             DoseLog.scheduled_at < end).order_by(DoseLog.scheduled_at, Medication.name)).all()
    return [{"id": d.id, "medication": m.name, "dosage": m.dosage, "instructions": m.instructions,
             "time": to_local(d.scheduled_at, tz).strftime("%H:%M"), "status": d.status,
             "resolved_by": d.resolved_by} for d, m in rows]


@router.post("/doses/{dose_id}/{action}")
def record_dose(dose_id: int, action: str, member: Member = Depends(WRITERS), db: Session = Depends(get_db)):
    if action not in ("take", "skip"):
        raise HTTPException(404, "Not found")
    dose = db.get(DoseLog, dose_id)
    med = db.get(Medication, dose.medication_id) if dose else None
    if med is None or med.profile_id != member.profile_id:
        raise HTTPException(404, "Not found")
    resolve_dose(db, dose, med, action, member.name)
    audit(db, member, f"dose_{action}", med.name)
    db.commit()
    return {"id": dose.id, "status": dose.status}


# ---------- appointments ----------

def _appt_out(a: Appointment, tz: ZoneInfo) -> dict:
    return {"id": a.id, "title": a.title, "doctor": a.doctor, "location": a.location, "notes": a.notes,
            "starts_at": iso(a.starts_at), "starts_at_local": to_local(a.starts_at, tz).strftime("%Y-%m-%dT%H:%M")}


@router.get("/appointments")
def list_appointments(member: Member = Depends(current_member), db: Session = Depends(get_db)):
    profile = profile_of(member, db)
    tz = ZoneInfo(profile.timezone)
    rows = db.scalars(select(Appointment).where(
        Appointment.profile_id == profile.id, Appointment.starts_at >= utcnow() - timedelta(hours=2))
        .order_by(Appointment.starts_at))
    return [_appt_out(a, tz) for a in rows]


@router.post("/appointments", status_code=201)
def add_appointment(body: schemas.AppointmentIn, member: Member = Depends(WRITERS), db: Session = Depends(get_db)):
    profile = profile_of(member, db)
    tz = ZoneInfo(profile.timezone)
    local = body.starts_at_local
    appt = Appointment(profile_id=profile.id, title=body.title, doctor=body.doctor, location=body.location,
                       notes=body.notes, starts_at=to_utc(local.date(), local.time(), tz))
    db.add(appt)
    db.flush()
    audit(db, member, "appointment_added", f"{appt.title} on {local:%d %b %H:%M}")
    db.commit()
    return _appt_out(appt, tz)


@router.delete("/appointments/{appt_id}", status_code=204)
def delete_appointment(appt_id: int, member: Member = Depends(WRITERS), db: Session = Depends(get_db)):
    appt = _owned(db, Appointment, appt_id, member)
    audit(db, member, "appointment_removed", appt.title)
    db.delete(appt)
    db.commit()


# ---------- insights, audit, agent ----------

@router.get("/insights")
def insights(member: Member = Depends(current_member), db: Session = Depends(get_db)):
    return build_insights(db, profile_of(member, db), utcnow())


@router.get("/audit")
def audit_log(member: Member = Depends(ELDER), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.profile_id == member.profile_id)
                      .order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(100))
    return [{"at": iso(r.at), "actor": r.actor, "role": r.actor_role, "action": r.action,
             "detail": r.detail} for r in rows]


@router.post("/agent/tick")
def trigger_agent(request: Request, member: Member = Depends(ELDER), db: Session = Depends(get_db)):
    """Run the reminder agent now (it also runs on a timer). Handy for demos."""
    state = request.app.state
    return vars(run_tick(db, state.mailer, state.settings))
