"""Runtime configuration.

Values are read, in order of precedence, from:

1. Real environment variables (and a local ``.env`` file loaded with python-dotenv).
2. Streamlit secrets (``.streamlit/secrets.toml`` locally, or the *Secrets* box on
   Streamlit Community Cloud).

No credentials are ever hard-coded. PostgreSQL is mandatory: there is no silent
fallback to SQLite or in-memory storage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from farmtrace.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_DOTENV_LOADED = False

SETTING_NAMES = (
    "DATABASE_URL",
    "PUBLIC_APP_URL",
    "APP_ENV",
    "APP_TIMEZONE",
    "ALLOW_DRIVER_SELF_ASSIGN",
    "LOG_LEVEL",
)


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    public_app_url: str | None
    app_env: str = "development"
    timezone: str = "Africa/Lagos"
    allow_driver_self_assign: bool = True
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ConfigurationError(
                "DATABASE_URL is not configured. Add your PostgreSQL connection string to the "
                ".env file (local) or to the app's Secrets on Streamlit Community Cloud."
            )
        return self.database_url


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env", override=False)
    except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
        pass
    _DOTENV_LOADED = True


def _streamlit_secrets() -> Mapping[str, object]:
    """Return Streamlit secrets as a plain mapping, or an empty mapping if none exist."""
    try:
        import streamlit as st

        return {key: st.secrets[key] for key in st.secrets.keys()}
    except Exception:  # No secrets.toml, or Streamlit not running — both are fine.
        return {}


def _parse_bool(value: str | None, name: str, default: bool) -> bool:
    if value is None or value == "":
        return default
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigurationError(f"{name} must be true or false.")


def normalize_database_url(url: str) -> str:
    """Normalise a PostgreSQL URL to the psycopg 3 SQLAlchemy driver.

    Hosted providers (Supabase, Neon, Render, Railway…) usually hand out
    ``postgres://`` or ``postgresql://`` URLs; both are accepted.
    """
    url = (url or "").strip()
    if not url:
        raise ConfigurationError("DATABASE_URL is empty.")
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    raise ConfigurationError(
        "DATABASE_URL must be a PostgreSQL connection string starting with postgresql://. "
        "FarmTrace does not fall back to SQLite or in-memory storage."
    )


def normalize_public_url(url: str | None) -> str | None:
    """Validate the public base URL that QR codes point to (no trailing slash)."""
    url = (url or "").strip()
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigurationError("PUBLIC_APP_URL must start with https:// (or http:// for local development).")
    if parts.query or parts.fragment:
        raise ConfigurationError("PUBLIC_APP_URL must not contain a query string or fragment.")
    return url.rstrip("/")


def load_settings(
    environ: Mapping[str, str] | None = None,
    secrets: Mapping[str, object] | None = None,
) -> Settings:
    """Build validated settings. ``environ``/``secrets`` can be injected in tests."""
    if environ is None:
        _load_dotenv_once()
        environ = os.environ
    if secrets is None:
        secrets = _streamlit_secrets()

    def read(name: str) -> str | None:
        value = environ.get(name)
        if value not in (None, ""):
            return str(value).strip()
        secret = secrets.get(name)
        if secret not in (None, ""):
            return str(secret).strip()
        return None

    app_env = (read("APP_ENV") or "development").lower()
    if app_env not in ("development", "production", "test", "demo"):
        raise ConfigurationError("APP_ENV must be one of development, production, test or demo.")

    timezone = read("APP_TIMEZONE") or "Africa/Lagos"
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigurationError(f"APP_TIMEZONE '{timezone}' is not a valid time zone.") from None

    database_url = read("DATABASE_URL")
    if database_url:
        database_url = normalize_database_url(database_url)

    return Settings(
        database_url=database_url,
        public_app_url=normalize_public_url(read("PUBLIC_APP_URL")),
        app_env=app_env,
        timezone=timezone,
        allow_driver_self_assign=_parse_bool(read("ALLOW_DRIVER_SELF_ASSIGN"), "ALLOW_DRIVER_SELF_ASSIGN", True),
        log_level=(read("LOG_LEVEL") or "INFO").upper(),
    )


def get_settings() -> Settings:
    """Load settings from the real environment and Streamlit secrets."""
    return load_settings()
