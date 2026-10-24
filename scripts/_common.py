"""Shared helpers for the command-line scripts (run them from the project root)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from farmtrace.config.settings import Settings, load_settings  # noqa: E402
from farmtrace.database.connection import (  # noqa: E402
    DatabaseStatus,
    check_database_ready,
    create_db_engine,
    make_session_factory,
)
from farmtrace.errors import ConfigurationError  # noqa: E402


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_ready_database() -> tuple[Settings, object, DatabaseStatus]:
    """Load settings and return (settings, session_factory, status); exit with a clear message if not ready."""
    try:
        settings = load_settings(secrets=_file_secrets())
        url = settings.require_database_url()
    except ConfigurationError as exc:
        fail(exc.user_message)
    engine = create_db_engine(url, pool_size=1)
    status = check_database_ready(engine)
    if not status.ok:
        fail(status.message)
    return settings, make_session_factory(engine), status


def _file_secrets() -> dict:
    """Read .streamlit/secrets.toml directly (scripts do not run inside Streamlit)."""
    path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    if not path.exists():
        return {}
    import tomllib

    with path.open("rb") as handle:
        return {k: v for k, v in tomllib.load(handle).items() if not isinstance(v, dict)}
