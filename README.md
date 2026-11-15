# CareCircle

A shared healthcare companion for elderly people. The elder stays in charge; caregivers
and family stay informed through passcode-based access and proactive email reminders.

## Run it

```bash
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
uvicorn app.main:create_app --factory --port 8000      # open http://localhost:8000
pytest                                                  # 34 tests
```

Emails are logged to the console unless SMTP is configured
(`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`).
Other settings: `TOKEN_TTL_MINUTES`, `GRACE_MINUTES`, `REMIND_BEFORE_MINUTES`, `TICK_SECONDS`.

**Database:** SQLite by default. For IBM Db2 set
`DATABASE_URL=db2+ibm_db://user:password@host:50000/DBNAME` and `pip install ibm_db_sa`.
The schema uses portable SQLAlchemy types only; it has not been run against a live Db2 instance.

## Architecture

| Layer | Module | Responsibility |
|---|---|---|
| API | `app/api.py`, `deps.py`, `schemas.py` | Routes, role guards, strict validation |
| Security | `security.py` | scrypt hashing, signed tokens, login throttling |
| Domain | `services/schedule.py` | Turns medicines into dose rows (timezone-aware, idempotent) |
| Agent | `services/agent.py` | Reminder + escalation loop |
| Insights | `services/insights.py`, `interactions.py` | Adherence, streaks, weak time slots, refills, interaction notes |
| Delivery | `services/mailer.py` | SMTP or console mailer |
| UI | `static/` | Accessible vanilla HTML/CSS/JS, no build step |

## Beyond a reminder app
- **Elder at the centre.** Only the elder manages the circle, rotates or revokes passcodes, and sees a full
  "who changed what" activity log. Family are read-only; caregivers cannot invite or remove people.
- **Escalation agent.** The elder is reminded before a dose is due; if it is still unconfirmed after the grace period
  it is marked missed and caregivers are told; three misses in 48h alert family. Appointment reminders go out 24h
  (everyone) and 2h (elder + caregivers) ahead. Refill alerts fire when about 3 days of pills remain.
- **Insights.** 7/30-day adherence, perfect-day streak, the weakest time of day, per-medicine rates, and an
  informational interaction/duplicate check when adding medicines.
- **Resilient reminders.** A notification ledger makes every email idempotent; failed sends retry on the next tick.

## Security
- Passcodes `TAG-SECRET` (~50 bits), stored as salted scrypt hashes, shown once; rotation signs out old sessions instantly.
- Login throttling per profile + client, identical errors for unknown profile / wrong passcode, constant-time verification.
- Every query is scoped to the caller's profile (tenant isolation tested); role guards on every write route.
- Strict Pydantic schemas (length limits, `extra="forbid"`), parameterised SQL, CSP / nosniff / frame-deny / no-store
  headers, and the UI renders data with `textContent` only.

## Accessibility
Landmarks and skip link, labelled fields with hints, 48px+ touch targets, visible focus, `aria-live` status messages,
larger-text and high-contrast modes (remembered; honours `prefers-contrast`), status shown by text + icon (never
colour alone), read-aloud of today's medicines, reduced-motion friendly.

## Known limits
- Login throttle is in-memory (use a shared store for multi-process deployments).
- The interaction list is a tiny curated sample, informational only, not medical advice.
- Profile creation is open; add CAPTCHA / rate limiting before public deployment.
- Roadmap: SMS / WhatsApp channels, elder approval of caregiver changes, voice-first input.
