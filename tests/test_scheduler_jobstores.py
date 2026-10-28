"""Tests for app.scheduler.build_jobstores.

Regression coverage for issue #14: the sync URL handed to
SQLAlchemyJobStore must name its driver explicitly (postgresql+psycopg2).
Since SQLAlchemy 2.1 a bare ``postgresql://`` URL defaults to the psycopg
(v3) driver, which is not installed, so the store silently fell back to
MemoryJobStore on every PostgreSQL deployment.
"""

import logging


def _engine_driver(store) -> str:
    """Drivername of the store's engine URL (password-hidden in repr)."""
    return store.engine.url.drivername


def test_postgres_async_url_becomes_psycopg2_jobstore():
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    from app.scheduler import build_jobstores

    stores = build_jobstores("postgresql+asyncpg://u:p@localhost:5432/quickly")
    assert set(stores) == {"default"}
    store = stores["default"]
    assert isinstance(store, SQLAlchemyJobStore)
    # Engine URL names the installed sync driver, not SQLAlchemy's default.
    assert _engine_driver(store) == "postgresql+psycopg2"
    assert store.engine.url.host == "localhost"
    assert store.engine.url.database == "quickly"


def test_legacy_postgres_scheme_is_also_psycopg2():
    from app.scheduler import build_jobstores

    stores = build_jobstores("postgres://u:p@db.internal:5432/quickly")
    assert set(stores) == {"default"}
    assert _engine_driver(stores["default"]) == "postgresql+psycopg2"


def test_sqlite_gets_memory_store():
    from app.scheduler import build_jobstores

    assert build_jobstores("sqlite+aiosqlite:///./local.db") == {}


def test_log_line_hides_password(caplog):
    from app.scheduler import build_jobstores

    with caplog.at_level(logging.INFO, logger="quickly.scheduler"):
        build_jobstores("postgresql+asyncpg://u:secretpw@localhost/quickly")

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "secretpw" not in text
    assert "***" in text
