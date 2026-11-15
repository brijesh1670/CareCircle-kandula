"""Application factory."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import Settings
from .db import make_engine, make_session_factory
from .security import LoginThrottle
from .services.agent import run_tick
from .services.mailer import Mailer, build_mailer

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def _tick_once(app: FastAPI) -> None:
    with app.state.session_factory() as db:
        run_tick(db, app.state.mailer, app.state.settings)


async def _agent_loop(app: FastAPI) -> None:
    while True:
        try:
            await asyncio.to_thread(_tick_once, app)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("reminder agent tick failed")
        await asyncio.sleep(app.state.settings.tick_seconds)


def create_app(settings: Settings | None = None, mailer: Mailer | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_agent_loop(app)) if settings.run_agent else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="CareCircle", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = make_session_factory(make_engine(settings.database_url))
    app.state.mailer = mailer or build_mailer(settings)
    app.state.throttle = LoginThrottle(settings.max_login_failures, settings.lockout_seconds)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update({
            "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app

