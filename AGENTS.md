<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

# Project rules

- The real product is the Python/Streamlit app (`app.py`, `pages/`, `farmtrace/`, `alembic/`, `scripts/`, `tests/`); `src/` is only the editor-preview notice page. Why: the editor preview cannot run Python.
- Python package lives in `farmtrace/`, not `src/`. Why: `src/` is reserved for the TanStack preview.
- Every permission and ownership check lives in `farmtrace/services/` (and `authentication/permissions.py`); pages only hide controls. Why: UI checks can be bypassed.
- Schema changes go through a new Alembic revision; never `create_all` at start-up. Why: production tables must be versioned and migrations explicit.
- PostgreSQL only at runtime; SQLite is allowed only inside the pytest fixtures. Why: no silent fallback storage.
- `transport_history` and `audit_logs` are append-only (DB triggers) and history rows are hash-linked via `tracking_service.record_event`. Why: tamper-evident trip records.
- Public QR pages read only through `qr_service`'s explicit public view. Why: keep private contact/driver data off public pages.
- Run `pytest` and, with a disposable PostgreSQL, `TEST_DATABASE_URL=... pytest` after Python changes. Why: triggers and migrations are PostgreSQL-only.
