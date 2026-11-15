"""Request validation. Every string is length-bounded; times and zones are checked."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254,
                                          pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileCreate(Strict):
    elder_name: Name
    elder_email: Email | None = None
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("Unknown timezone") from None
        return v


class LoginIn(Strict):
    profile_code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12)]
    passcode: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]


class MemberCreate(Strict):
    name: Name
    email: Email | None = None
    role: Literal["caregiver", "family"]


def _check_times(v: list[str]) -> list[str]:
    if not v or len(v) > 6 or not all(_HHMM.match(t) for t in v):
        raise ValueError("Provide 1-6 times in HH:MM (24h) format")
    return sorted(set(v))


class MedicationIn(Strict):
    name: Name
    dosage: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    instructions: Text = ""
    times: list[str]
    start_date: date | None = None
    end_date: date | None = None
    pills_remaining: int | None = Field(default=None, ge=0, le=9999)
    pills_per_dose: int = Field(default=1, ge=1, le=10)

    _times = field_validator("times")(_check_times)

    @model_validator(mode="after")
    def _dates(self) -> "MedicationIn":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class MedicationUpdate(Strict):
    name: Name | None = None
    dosage: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    instructions: Text | None = None
    times: list[str] | None = None
    end_date: date | None = None
    pills_remaining: int | None = Field(default=None, ge=0, le=9999)
    pills_per_dose: int | None = Field(default=None, ge=1, le=10)

    @field_validator("times")
    @classmethod
    def _times(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _check_times(v)


class AppointmentIn(Strict):
    title: Name
    doctor: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    location: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    starts_at_local: datetime  # wall-clock time in the profile's timezone

    @field_validator("starts_at_local")
    @classmethod
    def _naive(cls, v: datetime) -> datetime:
        if v.tzinfo is not None:
            raise ValueError("Send local time without a UTC offset")
        return v
