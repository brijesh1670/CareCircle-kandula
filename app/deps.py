"""FastAPI dependencies: DB session, authentication, role guards, audit helper."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .models import AuditLog, Member, Profile
from .security import decode_token

_bearer = HTTPBearer(auto_error=False)


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


def current_member(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                   db: Session = Depends(get_db)) -> Member:
    unauthorised = HTTPException(401, "Please sign in again", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorised
    claims = decode_token(request.app.state.settings.secret_key, creds.credentials)
    if not claims:
        raise unauthorised
    member = db.get(Member, int(claims["sub"]))
    # Rotating a passcode bumps token_version, instantly revoking older sessions.
    if member is None or member.token_version != claims.get("ver"):
        raise unauthorised
    return member


def require(*roles: str):
    def guard(member: Member = Depends(current_member)) -> Member:
        if member.role not in roles:
            raise HTTPException(403, "Your role does not allow this action")
        return member
    return guard


def profile_of(member: Member, db: Session) -> Profile:
    return db.get(Profile, member.profile_id)


def audit(db: Session, member: Member, action: str, detail: str = "") -> None:
    db.add(AuditLog(profile_id=member.profile_id, actor=member.name, actor_role=member.role,
                    action=action, detail=detail[:300]))
