from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.mailer import ConsoleMailer


@pytest.fixture()
def mailer():
    return ConsoleMailer()


@pytest.fixture()
def app(mailer):
    settings = Settings(database_url="sqlite://", secret_key="test-secret", run_agent=False,
                        max_login_failures=3, lockout_seconds=60)
    return create_app(settings, mailer)


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def db(app):
    with app.state.session_factory() as session:
        yield session


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def circle(client):
    """A profile with an elder, a caregiver and a family member, each with a token."""
    made = client.post("/api/profiles", json={"elder_name": "Asha", "elder_email": "asha@example.com",
                                              "timezone": "Asia/Kolkata"}).json()
    code = made["profile_code"]
    tokens = {"elder": client.post("/api/auth/login", json={"profile_code": code, "passcode": made["passcode"]}).json()["token"]}
    for role, email in (("caregiver", "care@example.com"), ("family", "kin@example.com")):
        m = client.post("/api/members", headers=auth(tokens["elder"]),
                        json={"name": role.title(), "email": email, "role": role}).json()
        tokens[role] = client.post("/api/auth/login", json={"profile_code": code, "passcode": m["passcode"]}).json()["token"]
    return {"code": code, "tokens": tokens, "passcode": made["passcode"]}


def naive_utc(**delta) -> datetime:
    from app.models import utcnow
    return utcnow() + timedelta(**delta)
