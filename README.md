# FarmTrace — Farm Produce Transportation & Traceability (Nigeria)

FarmTrace is a **Python / Streamlit** web application backed by **PostgreSQL**.

- **Farmers** register farms and produce batches, request transport and print QR labels.
- **Administrators** approve requests, assign verified drivers, manage users and roles, and run reports.
- **Drivers** see only their own assignments and record each stage of the journey.
- **Buyers / the public** scan a batch's QR code to see where it came from, without signing in.

> **Two separate things live in this repository.**
> 1. **The FarmTrace application** (Python): `app.py`, `pages/`, `farmtrace/`, `alembic/`, `scripts/`, `tests/`.
>    Run it with `streamlit run app.py`. This is the deliverable.
> 2. **The Lovable editor preview** (React/TanStack, in `src/`, `package.json`, `vite.config.ts` …).
>    It only shows a notice page explaining how to run FarmTrace. It is **not** the FarmTrace app and it
>    cannot run Python. Run it (optional) with `npm install && npm run dev`. Streamlit ignores these files.

---

## Contents

1. [Requirements](#1-requirements)
2. [Local setup](#2-local-setup)
3. [Creating the PostgreSQL database](#3-creating-the-postgresql-database)
4. [Configuration and secrets](#4-configuration-and-secrets)
5. [Migrations](#5-migrations)
6. [First administrator](#6-first-administrator)
7. [Running the app](#7-running-the-app)
8. [Running the tests](#8-running-the-tests)
9. [Demo data (optional)](#9-demo-data-optional)
10. [Deploying to Streamlit Community Cloud](#10-deploying-to-streamlit-community-cloud)
11. [How it works](#11-how-it-works)
12. [Security model](#12-security-model)
13. [Troubleshooting](#13-troubleshooting)
14. [Status: what is built, tested and still manual](#14-status-what-is-built-tested-and-still-manual)
15. [Known limitations](#15-known-limitations)

---

## 1. Requirements

- Python **3.11 or newer** (tested on 3.13)
- A **PostgreSQL** database (version 14+; tested on 17). Hosted providers such as Supabase, Neon, Render
  or Railway work. FarmTrace never falls back to SQLite or in-memory storage.
- Git, and a GitHub account for deployment

## 2. Local setup

```bash
git clone <your-repository-url> farmtrace
cd farmtrace

python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
```

Then edit `.env` (next sections) and continue with migrations.

## 3. Creating the PostgreSQL database

**A database is not created for you.** You must provision one yourself. Example with Supabase (free tier):

1. Sign in at <https://supabase.com> → **New project**. Choose a strong database password and a region
   close to your users (e.g. *West EU* or *Cape Town* for Nigeria). Save the password somewhere safe.
2. When the project is ready, click **Connect** (top bar).
3. Copy the **Session pooler** connection string. It looks like:
   `postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-eu-west-1.pooler.supabase.com:5432/postgres`
4. Replace `[YOUR-PASSWORD]` with your password and add `?sslmode=require` at the end.
   If your password contains `@ : / ? # &` or spaces, URL-encode it (for example `@` → `%40`).

Other providers (Neon, Render, Railway…) give you an equivalent `postgresql://` or `postgres://` URL; both
forms are accepted. The *transaction pooler* (port 6543) also works, because FarmTrace disables
server-side prepared statements.

## 4. Configuration and secrets

FarmTrace reads settings from, in order: real environment variables → a local `.env` file →
Streamlit secrets (`.streamlit/secrets.toml` locally, or **App settings → Secrets** on Streamlit Cloud).
`.env` and `.streamlit/secrets.toml` are git-ignored. **Never commit real credentials.**

| Setting | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL connection string (see above). |
| `PUBLIC_APP_URL` | yes, for QR codes | Public address of the app, no trailing slash, e.g. `https://farmtrace.streamlit.app`. Locally: `http://localhost:8501`. QR codes point to `PUBLIC_APP_URL/?verify=<token>`. |
| `APP_ENV` | no | `development` (default), `production`, `test` or `demo`. Demo data is refused when `production`. |
| `APP_TIMEZONE` | no | Default `Africa/Lagos`. Used for dates on screen and in reports. |
| `ALLOW_DRIVER_SELF_ASSIGN` | no | `true` (default) lets available drivers accept approved requests themselves. `false` = only admins assign. |
| `LOG_LEVEL` | no | `INFO` (default), `DEBUG`, `WARNING`, `ERROR`. |

Templates with placeholder values only: `.env.example` and `.streamlit/secrets.toml.example`.

If `DATABASE_URL` is missing, the database is unreachable, or the tables have not been created, the app
shows a clear setup message (never the password or connection string) and stops.

## 5. Migrations

Tables are **never** created automatically at start-up. They are managed with Alembic:

```bash
alembic upgrade head            # create / upgrade all tables (safe to re-run)
python scripts/check_database.py   # confirm: connection OK, revision 0001 (latest: 0001)
```

Alembic reads `DATABASE_URL` from the same places as the app; `alembic.ini` contains no credentials.
Other useful commands: `alembic current`, `alembic history`, `alembic downgrade -1` (destructive — avoid on
production).

Migration `0001_initial_schema` creates every table, constraint and index, plus PostgreSQL triggers that make
the delivery history and the audit log append-only.

## 6. First administrator

Public registration can **never** create an administrator. Create the first one from a terminal that has
`DATABASE_URL` configured:

```bash
python scripts/create_admin.py --email admin@yourdomain.ng --name "Ada Okafor"
```

You are prompted for the password (not echoed). For automation set `FARMTRACE_ADMIN_PASSWORD` instead.
If the email already belongs to an account, that account is given the administrator role. Afterwards,
administrators can grant or remove roles from the **Admin dashboard → Users & roles** tab.

Passwords must be 8+ characters with at least one letter and one number. They are stored only as Argon2
hashes.

## 7. Running the app

```bash
streamlit run app.py
```

Open <http://localhost:8501>. Register a farmer and a driver from **Create account**, sign in as the
administrator in another browser window, and walk through the workflow (section 11).

## 8. Running the tests

```bash
pytest                          # fast: isolated in-memory SQLite, no credentials needed
```

To also verify PostgreSQL compatibility (the real migration, append-only triggers, constraints), create an
**empty, dedicated** database whose name contains `test`, then:

```bash
TEST_DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/farmtrace_test pytest
```

> Every table in the test database is emptied during the run. Never point it at production data. The test
> suite refuses to run if the database name does not contain `test`.

Results from the last run in the build environment (PostgreSQL 17.9, Python 3.13):
`pytest` → **74 passed, 2 skipped** (the 2 PostgreSQL-only tests); with `TEST_DATABASE_URL` → **76 passed**.

Coverage: registration/login/password hashing, role assignment and admin-registration prevention,
database constraints and relationships, farm and produce ownership, transport requests and driver
assignment, valid and invalid status transitions, delays, tracking history hash-chain and tamper detection,
audit records, QR token uniqueness/revocation/verification, public QR privacy, CSV generation and
spreadsheet-formula neutralisation, configuration and database-connection errors, demo-data labelling.

## 9. Demo data (optional)

Use a **separate** demo database — never your production one.

```bash
APP_ENV=demo DATABASE_URL=postgresql://.../farmtrace_demo python scripts/load_demo_data.py
```

Every demo record is flagged `is_demo`, demo accounts use `@example.com` addresses, and the app shows a
"DEMO" label. The script refuses to run when `APP_ENV=production` and cannot be loaded twice.

## 10. Deploying to Streamlit Community Cloud

1. **Provision the production database** (section 3) and keep its connection string private.
2. **Create the tables** from your own computer, pointing at the production database:
   ```bash
   DATABASE_URL="postgresql://...?...sslmode=require" alembic upgrade head
   DATABASE_URL="postgresql://...?...sslmode=require" python scripts/create_admin.py --email you@domain.ng --name "Your Name"
   DATABASE_URL="postgresql://...?...sslmode=require" python scripts/check_database.py
   ```
3. **Push the repository to GitHub** (public or private). Required files are listed below.
4. Go to <https://share.streamlit.io> → **Create app** → **Deploy a public app from GitHub**. Choose your
   repository and branch, set **Main file path** to `app.py`, and pick your app URL.
5. Open **Advanced settings**: choose Python **3.11+**, and paste into **Secrets**:
   ```toml
   DATABASE_URL = "postgresql://USERNAME:PASSWORD@HOST:5432/DATABASE_NAME?sslmode=require"
   PUBLIC_APP_URL = "https://your-app-name.streamlit.app"
   APP_ENV = "production"
   APP_TIMEZONE = "Africa/Lagos"
   ALLOW_DRIVER_SELF_ASSIGN = "true"
   LOG_LEVEL = "INFO"
   ```
   `PUBLIC_APP_URL` must be exactly the URL you chose (no trailing slash), otherwise printed QR codes will
   point to the wrong address.
6. Click **Deploy**. When it starts, sign in as the administrator you created in step 2.
7. Later schema changes: run `alembic upgrade head` against production **before** deploying the new code.
   If you change secrets, use **Reboot app**.

Records live in PostgreSQL, so they survive app restarts, sleeps and redeployments. Free Streamlit apps
sleep after inactivity; the first visit afterwards (including a QR scan) takes a little longer to wake up.

### Files needed in the GitHub repository

```text
app.py
requirements.txt
README.md
.gitignore
.env.example
alembic.ini
alembic/env.py
alembic/script.py.mako
alembic/versions/0001_initial_schema.py
.streamlit/config.toml
.streamlit/secrets.toml.example
farmtrace/            (all .py files: authentication, components, config, database, services, utils)
pages/                (1_Login.py … 10_Verify_Produce.py)
scripts/              (_common.py, check_database.py, create_admin.py, load_demo_data.py)
tests/                (conftest.py and test_*.py)
pytest.ini
```

Do **not** commit `.env`, `.streamlit/secrets.toml`, `__pycache__/` or `.venv/` (already in `.gitignore`).
The React preview files (`src/`, `package.json`, etc.) are harmless to Streamlit and can stay or be removed.

## 11. How it works

### Workflow

1. A farmer registers a **farm** (Farmer dashboard).
2. The farmer registers a **produce batch** (produce, category, quantity, unit, harvest date, pickup and
   destination). A unique QR code is generated.
3. The farmer creates a **transport request** for the batch.
4. An **administrator** approves it (optionally with cost and estimated delivery) and **assigns** an eligible
   driver — or, if enabled, an available driver accepts it.
5. The **driver** sees the assignment and records **Dispatched → In-Transit → Delivered**, typing the current
   location at each step.
6. Every change is stored in the delivery history with its time and the responsible user.
7. The farmer and administrators follow progress on **Delivery tracking**; buyers see the public status by QR.

### Delivery stages

```text
Pending -> Approved -> Assigned -> Dispatched -> In-Transit -> Delivered
   \__________\__________\___________\______________\--> Cancelled (by admin; farmer only before dispatch)
```

Any other jump is rejected by the service layer. **Delayed** is a separate flag that can be raised and
resolved while a delivery is Assigned, Dispatched or In-Transit; the underlying stage is kept, so the trip
resumes where it was. Drivers can also log checkpoints without changing the stage.

### Project layout

```text
app.py                     entry point: config + database check, sign-in restore, role-based navigation
pages/                     one Streamlit page per screen
farmtrace/authentication/  registration, login, Argon2 hashing, role checks (CurrentUser)
farmtrace/services/        all business rules and permission checks (farmer, transport, driver,
                           tracking, qr, report, admin, audit)
farmtrace/database/        SQLAlchemy models, connection/pooling, readiness check, demo seed
farmtrace/components/      shared Streamlit UI pieces (theme, sidebar, tables, guards)
farmtrace/config/          settings loader (env, .env, Streamlit secrets)
farmtrace/utils/           validators (Nigerian phone numbers, dates, quantities…), helpers, logging
alembic/                   database migrations
scripts/                   create_admin, check_database, load_demo_data
tests/                     pytest suite
```

### Database tables

`users` (email, active flag) · `user_credentials` (password hash, kept separate) · `user_profiles` (name,
phone) · `user_roles` (one row per role) · `drivers` (licence, vehicle, capacity, availability) · `farms` ·
`produce_batches` (incl. unique `qr_token`) · `revoked_qr_tokens` · `transport_requests` ·
`transport_history` (append-only, hash-linked) · `audit_logs` (append-only).

## 12. Security model

- Every service function re-checks the signed-in user's roles and record ownership. Hiding a button in the
  page is only a convenience; calling a service directly without permission raises an error.
- Farmers see only their own farms, batches and requests. Drivers see only deliveries assigned to them (plus
  open requests they may accept, when enabled). Administrators see everything.
- Streamlit's session only remembers the signed-in user's id; the user is re-loaded from the database on each
  page run, so deactivations and role changes take effect immediately.
- The public verification page shows only: produce, category, quantity, harvest date, batch reference, farm
  name, LGA/state, destination market, dispatch/delivery times, current status and a public journey
  summary. It never shows names of people, phone numbers, emails, vehicle details, internal ids or tokens of
  other batches. Unknown, malformed and revoked codes get a safe message.
- QR tokens are 256-bit random values (`secrets.token_urlsafe(32)`), unique in the database, and can be revoked
  or replaced.
- Errors shown to users never include stack traces or credentials; details go to the server log with a short
  reference code.
- CSV exports neutralise values starting with `= + - @` so they cannot run as spreadsheet formulas.

**Email confirmation and password-recovery emails are not implemented** (they need an external email service).
Instead, an administrator can issue a one-time temporary password from **Admin dashboard → Users & roles**
after verifying the person's identity, and users can change their password under **My account**.

## 13. Troubleshooting

| Message / problem | Fix |
|---|---|
| *Database not configured* | Add `DATABASE_URL` to `.env` (local) or Secrets (Streamlit Cloud). |
| *Database unavailable* | Check host, password (URL-encode special characters), `?sslmode=require`, and that the provider project isn't paused. Run `python scripts/check_database.py`. |
| *Database tables not created yet* / *needs an upgrade* | Run `alembic upgrade head` against that database. |
| *Configuration problem* | A setting has an invalid value (e.g. `APP_ENV`, `APP_TIMEZONE`, `PUBLIC_APP_URL` without `https://`). |
| QR code opens the wrong site | `PUBLIC_APP_URL` doesn't match the deployed address. Fix it and reboot; existing QR codes keep their token, so re-download the labels. |
| `ModuleNotFoundError` | Activate the virtual environment and `pip install -r requirements.txt`. |
| Signed out after refreshing the page | Expected: Streamlit sessions end on a full page reload. Sign in again. |

## 14. Status: what is built, tested and still manual

**Built and tested automatically** (pytest on SQLite and PostgreSQL 17): everything listed in section 8.

**Tested by hand in a browser** (local Streamlit + local PostgreSQL 17 in the build environment): farmer
registration → farm → batch → transport request; driver registration; administrator sign-in → approve →
assign; driver Dispatched → In-Transit → Delivered; farmer delivery tracking with full history; admin
history-integrity check; reports page; public QR verification with a valid code (no private data shown) and
an invalid code (safe message).

**Not done — requires you:**
- Create the production PostgreSQL database (no hosted database has been created or connected).
- Run `alembic upgrade head` and `scripts/create_admin.py` against it.
- Push to GitHub and deploy on Streamlit Community Cloud with the secrets above. The app has **not** been
  deployed or tried on Streamlit Cloud yet.
- Optional: an email provider if you want email confirmation / password-reset emails.

## 15. Known limitations

- Driver locations are typed in by hand; there is no GPS tracking.
- The delivery history is **tamper-evident, not tamper-proof**. Each event stores a SHA-256 hash of its
  contents and of the previous event, and PostgreSQL triggers block updates and deletes, so edits by normal
  means are blocked and silent edits are detected by *History integrity*. Someone with full database-owner
  rights could still drop the triggers and rewrite a whole chain consistently. **This is not a blockchain.**
  Exporting history reports regularly lets you compare hashes later.
- Refreshing the browser signs you out (Streamlit sessions are per browser tab connection).
- No email notifications, SMS or payments.
- Free Streamlit Cloud apps sleep when idle and wake on the next visit.
