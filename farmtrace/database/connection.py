"""Engine, session and database-readiness handling.

* One SQLAlchemy engine (connection pool) per database URL per process — the
  Streamlit layer additionally caches it with ``st.cache_resource``.
* ``session_scope`` wraps a unit of work in a transaction: commit on success,
  rollback on any exception, always close.
* Tables are **never** created automatically. ``check_database_ready`` tells the
  interface whether migrations have been applied.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from farmtrace.config.settings import PROJECT_ROOT, normalize_database_url
from farmtrace.utils.logger import get_logger

log = get_logger(__name__)
_ENGINES: dict[str, Engine] = {}


def create_db_engine(database_url: str, *, pool_size: int = 5) -> Engine:
    """Create a pooled PostgreSQL engine.

    ``prepare_threshold=None`` disables server-side prepared statements, which keeps
    the app compatible with PgBouncer/Supabase *transaction* poolers.
    """
    url = normalize_database_url(database_url)
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=5,
        pool_recycle=1800,
        connect_args={"connect_timeout": 10, "application_name": "farmtrace", "prepare_threshold": None},
    )


def get_engine(database_url: str) -> Engine:
    """Return the process-wide engine for ``database_url`` (created on first use)."""
    url = normalize_database_url(database_url)
    if url not in _ENGINES:
        _ENGINES[url] = create_db_engine(url)
    return _ENGINES[url]


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Tests only: SQLite enforces foreign keys only when asked to."""

    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False lets the interface read loaded objects after the
    # transaction has committed and the session has been closed.
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass(frozen=True)
class DatabaseStatus:
    ok: bool
    code: str  # "ready" | "unreachable" | "not_migrated" | "outdated"
    message: str
    current_revision: str | None = None
    head_revision: str | None = None


def migration_head() -> str | None:
    """The newest Alembic revision shipped with this code."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


def check_database_ready(engine: Engine) -> DatabaseStatus:
    """Verify connectivity and that ``alembic upgrade head`` has been applied."""
    head = migration_head()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            if not inspect(connection).has_table("alembic_version"):
                return DatabaseStatus(
                    False,
                    "not_migrated",
                    "The database is reachable but its tables have not been created. "
                    "Run `alembic upgrade head` (see README, section 13).",
                    None,
                    head,
                )
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError as exc:
        log.error("Database connectivity check failed: %s", exc.__class__.__name__)
        log.debug("Connectivity failure detail", exc_info=True)
        return DatabaseStatus(
            False,
            "unreachable",
            "The database could not be reached. Check DATABASE_URL, network access and that the "
            "database server is running.",
            None,
            head,
        )
    if current != head:
        return DatabaseStatus(
            False,
            "outdated",
            "The database schema is out of date. Run `alembic upgrade head` to apply the latest migrations.",
            current,
            head,
        )
    return DatabaseStatus(True, "ready", "Database ready.", current, head)
