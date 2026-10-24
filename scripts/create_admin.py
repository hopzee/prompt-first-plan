"""Create the first FarmTrace administrator (or promote an existing account).

Administrator accounts can never be created through the website's public
registration form. Run this once from a terminal that has DATABASE_URL set:

    python scripts/create_admin.py --email admin@yourdomain.ng --name "Ada Okafor"

You are prompted for the password (it is not echoed and never stored in shell
history). For non-interactive use, set FARMTRACE_ADMIN_PASSWORD instead.
If the email already belongs to an account, that account is given the admin role.
"""

from __future__ import annotations

import argparse
import getpass
import os

from _common import fail, load_ready_database

from farmtrace.authentication.auth import bootstrap_administrator
from farmtrace.database import repository
from farmtrace.errors import FarmTraceError


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a FarmTrace administrator.")
    parser.add_argument("--email", required=True, help="Administrator email address")
    parser.add_argument("--name", help="Full name (required when creating a new account)")
    parser.add_argument("--phone", help="Optional Nigerian phone number")
    args = parser.parse_args()

    _, factory, _ = load_ready_database()

    with factory() as session:
        exists = repository.get_user_by_email(session, args.email.strip().lower()) is not None

    password = None
    if not exists:
        password = os.environ.get("FARMTRACE_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("New administrator password: ")
            if password != getpass.getpass("Repeat password: "):
                fail("The passwords do not match.")

    with factory() as session:
        try:
            user, created = bootstrap_administrator(
                session, email=args.email, full_name=args.name, password=password, phone=args.phone
            )
            session.commit()
        except FarmTraceError as exc:
            session.rollback()
            fail(exc.user_message)
        verb = "Created administrator" if created else "Granted administrator role to existing account"
        print(f"{verb}: {user.email}")


if __name__ == "__main__":
    main()
