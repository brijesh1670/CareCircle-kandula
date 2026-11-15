"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///carecircle.db"
    secret_key: str = ""
    token_ttl_minutes: int = 480
    grace_minutes: int = 30          # how long after the scheduled time a dose is "missed"
    remind_before_minutes: int = 15  # reminder lead time for doses
    tick_seconds: int = 60           # reminder-agent cadence
    max_login_failures: int = 5
    lockout_seconds: int = 300
    smtp_host: str = ""              # empty -> emails are logged, not sent
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = "carecircle@localhost"
    run_agent: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.getenv("SECRET_KEY", "")
        if not secret:
            secret = secrets.token_urlsafe(32)
            log.warning("SECRET_KEY not set: using a random key; sessions reset on restart.")
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            secret_key=secret,
            token_ttl_minutes=int(os.getenv("TOKEN_TTL_MINUTES", cls.token_ttl_minutes)),
            grace_minutes=int(os.getenv("GRACE_MINUTES", cls.grace_minutes)),
            remind_before_minutes=int(os.getenv("REMIND_BEFORE_MINUTES", cls.remind_before_minutes)),
            tick_seconds=int(os.getenv("TICK_SECONDS", cls.tick_seconds)),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", cls.smtp_port)),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            mail_from=os.getenv("MAIL_FROM", cls.mail_from),
            run_agent=os.getenv("RUN_AGENT", "1") != "0",
        )
