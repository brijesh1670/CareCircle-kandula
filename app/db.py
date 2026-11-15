"""Engine / session factory. `DATABASE_URL` selects the backend:
  sqlite:///carecircle.db                         (default)
  postgresql://user:pass@host/dbname              (hosted Postgres, needs psycopg)
  db2+ibm_db://user:pass@host:50000/DBNAME        (IBM Db2, needs ibm_db_sa)
"""
from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def normalise_url(url: str) -> str:
    """Hosts like Render/Heroku hand out postgres:// URLs; SQLAlchemy needs an explicit driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str) -> Engine:
    url = normalise_url(url)
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # one shared in-memory DB (tests)
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
