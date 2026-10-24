"""Load clearly-labelled DEMO records into a NON-production database.

    APP_ENV=demo python scripts/load_demo_data.py

Refuses to run when APP_ENV=production. Use a separate demo database so demo
records never mix with real ones. All demo accounts share the password you are
prompted for (or FARMTRACE_DEMO_PASSWORD).
"""

from __future__ import annotations

import getpass
import os

from _common import fail, load_ready_database

from farmtrace.database.seed import load_demo_data
from farmtrace.errors import FarmTraceError


def main() -> None:
    settings, factory, _ = load_ready_database()
    if settings.is_production:
        fail("APP_ENV is 'production'. Demo data must never be loaded into the production database.")

    password = os.environ.get("FARMTRACE_DEMO_PASSWORD") or getpass.getpass("Password for all demo accounts: ")

    with factory() as session:
        try:
            summary = load_demo_data(session, password)
            session.commit()
        except FarmTraceError as exc:
            session.rollback()
            fail(exc.user_message)

    print("Demo data loaded (every record is marked DEMO).")
    print("Demo accounts:", ", ".join(summary.accounts))
    print("Demo batches:", ", ".join(summary.batch_numbers))
    print("Demo transport requests:", ", ".join(summary.request_numbers))
    base = settings.public_app_url or "<PUBLIC_APP_URL>"
    for number, token in zip(summary.batch_numbers, summary.qr_tokens):
        print(f"Verify {number}: {base}/?verify={token}")


if __name__ == "__main__":
    main()
