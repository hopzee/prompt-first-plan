"""Streamlit runtime glue: configuration, database sessions, sign-in state and safe actions.

Streamlit ``session_state`` only remembers *which* user is signed in (their id).
All data lives in PostgreSQL, and the user record is re-loaded from the database
on every page run so deactivations and role changes take effect immediately.
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from farmtrace.authentication.auth import load_current_user
from farmtrace.authentication.permissions import CurrentUser
from farmtrace.config.settings import Settings, load_settings
from farmtrace.database.connection import (
    DatabaseStatus,
    check_database_ready,
    create_db_engine,
    make_session_factory,
    session_scope,
)
from farmtrace.errors import ConfigurationError, DatabaseUnavailableError, FarmTraceError
from farmtrace.utils.logger import configure_logging, get_logger

log = get_logger(__name__)
T = TypeVar("T")

USER_KEY = "farmtrace_user_id"
_CURRENT_USER_KEY = "_farmtrace_current_user"
_FLASH_KEY = "_farmtrace_flash"
AFTER_LOGIN_KEY = "_farmtrace_after_login"
AFTER_LOGOUT_KEY = "_farmtrace_after_logout"


# ------------------------------------------------------------------ resources
@st.cache_resource(show_spinner=False, ttl=120)
def _settings() -> Settings:
    return load_settings()


@st.cache_resource(show_spinner=False)
def _session_factory(database_url: str):
    return make_session_factory(create_db_engine(database_url))


@st.cache_data(show_spinner=False, ttl=30)
def _readiness(database_url: str) -> DatabaseStatus:
    factory = _session_factory(database_url)
    return check_database_ready(factory.kw["bind"])


def settings() -> Settings:
    return _settings()


def _setup_error(title: str, message: str, steps: list[str] | None = None) -> None:
    st.error(f"**{title}**\n\n{message}", icon=":material/error:")
    if steps:
        st.markdown("**How to fix it**\n\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1)))
    st.caption("See the README section *Troubleshooting* for more help.")
    st.stop()


def boot() -> Settings:
    """Validate configuration and database readiness, or stop with a helpful message."""
    try:
        config = settings()
    except ConfigurationError as exc:
        _setup_error("Configuration problem", exc.user_message)
    configure_logging(config.log_level)
    if not config.database_url:
        _setup_error(
            "Database not configured",
            "FarmTrace stores all records in PostgreSQL and needs a `DATABASE_URL` to start.",
            [
                "Create a PostgreSQL database (for example a free Supabase or Neon project).",
                "Copy its connection string.",
                "Locally: put `DATABASE_URL=...` in a `.env` file. On Streamlit Community Cloud: "
                "open *App settings → Secrets* and add `DATABASE_URL = \"...\"`.",
                "Run `alembic upgrade head` once to create the tables, then reload this page.",
            ],
        )
    status = _readiness(config.database_url)
    if not status.ok:
        _readiness.clear()  # re-check on the next page load instead of caching the failure
        title = {
            "unreachable": "Database unavailable",
            "not_migrated": "Database tables not created yet",
            "outdated": "Database needs an upgrade",
        }.get(status.code, "Database problem")
        _setup_error(title, status.message)
    return config


@contextmanager
def db() -> Iterator[Session]:
    with session_scope(_session_factory(settings().require_database_url())) as session:
        yield session


# ------------------------------------------------------------------ sign-in state
def resolve_current_user() -> CurrentUser | None:
    user_id = st.session_state.get(USER_KEY)
    user = None
    if user_id is not None:
        user = query(lambda s: load_current_user(s, user_id))
        if user is None:
            st.session_state.pop(USER_KEY, None)
            flash("Your session ended because your account is no longer active.", "warning")
    st.session_state[_CURRENT_USER_KEY] = user
    return user


def current_user() -> CurrentUser | None:
    return st.session_state.get(_CURRENT_USER_KEY)


def sign_in(user: CurrentUser) -> None:
    st.session_state[USER_KEY] = user.id
    st.session_state[AFTER_LOGIN_KEY] = True
    flash(f"Welcome, {user.full_name.split()[0]}!", "success")


def sign_out() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state[AFTER_LOGOUT_KEY] = True
    flash("You have been signed out.", "info")


# ------------------------------------------------------------------ messages
def flash(message: str, kind: str = "success") -> None:
    """Queue a message that survives the next ``st.rerun()``."""
    st.session_state.setdefault(_FLASH_KEY, []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop(_FLASH_KEY, []):
        {"success": st.success, "warning": st.warning, "info": st.info, "error": st.error}.get(kind, st.info)(message)


# ------------------------------------------------------------------ safe actions
def _handle(exc: Exception) -> None:
    if isinstance(exc, FarmTraceError):
        st.error(exc.user_message, icon=":material/error:")
    elif isinstance(exc, SQLAlchemyError):
        log.error("Database error: %s", exc.__class__.__name__, exc_info=True)
        st.error(DatabaseUnavailableError.default_message, icon=":material/cloud_off:")
    else:
        reference = secrets.token_hex(4)
        log.error("Unexpected error (reference %s)", reference, exc_info=True)
        st.error(f"Something went wrong. Please try again. (Reference: {reference})", icon=":material/error:")


def query(action: Callable[[Session], T], default: T | None = None) -> T | None:
    """Run a read inside its own transaction; show a friendly error on failure."""
    try:
        with db() as session:
            return action(session)
    except Exception as exc:  # noqa: BLE001 - every failure is shown safely and logged
        _handle(exc)
        return default


def act(action: Callable[[Session], T], *, success: str | None = None, rerun: bool = True) -> T | None:
    """Run a write in one transaction. On success: flash a message and (by default) rerun."""
    try:
        with db() as session:
            result = action(session)
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
        return None
    if success:
        flash(success, "success")
    if rerun:
        st.rerun()
    return result
