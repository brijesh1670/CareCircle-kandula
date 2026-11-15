"""Email delivery. SMTP when configured, otherwise a console/outbox mailer for demos."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from ..config import Settings

log = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleMailer:
    """Logs emails and keeps them in `outbox` (used by tests and local demos)."""

    def __init__(self) -> None:
        self.outbox: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.outbox.append((to, subject, body))
        log.info("EMAIL to=%s subject=%s", to, subject)


class SMTPMailer:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self._s.mail_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self._s.smtp_host, self._s.smtp_port, timeout=10) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if self._s.smtp_user:
                smtp.login(self._s.smtp_user, self._s.smtp_password)
            smtp.send_message(msg)


def build_mailer(settings: Settings) -> Mailer:
    return SMTPMailer(settings) if settings.smtp_host else ConsoleMailer()
