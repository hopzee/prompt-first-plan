"""Check that DATABASE_URL is reachable and fully migrated.

    python scripts/check_database.py

Prints the PostgreSQL server version and migration revision. Never prints the
connection string or password.
"""

from __future__ import annotations

from _common import fail, load_ready_database
from sqlalchemy import text

from farmtrace.config.settings import Settings  # noqa: F401  (import check)


def main() -> None:
    settings, factory, status = load_ready_database()
    with factory() as session:
        version = session.execute(text("SHOW server_version")).scalar()
        database = session.execute(text("SELECT current_database()")).scalar()
        users = session.execute(text("SELECT count(*) FROM users")).scalar()
    if version is None:
        fail("Connected, but the server did not report a version.")
    print("Database connection: OK")
    print(f"PostgreSQL server version: {version}")
    print(f"Database name: {database}")
    print(f"Migration revision: {status.current_revision} (latest: {status.head_revision})")
    print(f"User accounts stored: {users}")
    print(f"APP_ENV: {settings.app_env}")
    print(f"PUBLIC_APP_URL: {settings.public_app_url or 'NOT SET — QR codes cannot be generated'}")


if __name__ == "__main__":
    main()
