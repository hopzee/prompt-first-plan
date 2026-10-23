"""Administrator dashboard: metrics, charts, users & roles, drivers, audit log, integrity checks."""

import datetime as dt

import pandas as pd
import plotly.express as px
import streamlit as st

from farmtrace import domain
from farmtrace.components import guards, runtime
from farmtrace.components.dashboard_cards import empty_state, metric_cards, page_header, timeline
from farmtrace.components.deliveries import history_events
from farmtrace.components.tables import csv_download, searchable_table
from farmtrace.services import admin_service, driver_service, report_service, tracking_service
from farmtrace.services.transport_service import RequestFilters
from farmtrace.utils.helpers import format_datetime, local_today, mask_identifier

GREEN = ["#2E7D32", "#7A5230", "#E8A33D", "#1D4E89", "#00594F", "#9A2A1E", "#6A7365"]

user = guards.require_access(domain.ROLE_ADMIN)
tz = runtime.settings().timezone

page_header("Administration", "Live figures from the FarmTrace database.")

metrics = runtime.query(lambda s: report_service.admin_metrics(s, user), {}) or {}
metric_cards([
    ("Registered users", metrics.get("users", 0), f"{metrics.get('active_users', 0)} active"),
    ("Farmers", metrics.get("farmers", 0), None),
    ("Drivers", metrics.get("drivers", 0), f"{metrics.get('driver_profiles', 0)} with vehicle profiles"),
    ("Produce batches", metrics.get("batches", 0), None),
    ("Pending requests", metrics.get("pending", 0), f"{metrics.get('approved', 0)} approved, awaiting a driver"),
    ("Active deliveries", metrics.get("active", 0), "Assigned, dispatched or in transit"),
    ("Completed deliveries", metrics.get("completed", 0), None),
    ("Delayed deliveries", metrics.get("delayed", 0), "Active deliveries with an open delay"),
])

tab_overview, tab_users, tab_drivers, tab_audit, tab_integrity = st.tabs(
    ["Overview", "Users & roles", "Drivers", "Audit log", "History integrity"]
)

with tab_overview:
    c1, c2 = st.columns(2)
    start = c1.date_input("From", value=local_today() - dt.timedelta(days=30), key="admin_from")
    end = c2.date_input("To", value=local_today(), key="admin_to")
    report = runtime.query(
        lambda s: report_service.transport_report(s, user, RequestFilters(date_from=start, date_to=end), tz_name=tz)
    )
    if report is None or report.empty:
        empty_state("No transport requests in this period", "Charts appear once requests exist in the database.")
    else:
        st.caption(f"Based on {len(report)} transport request(s) created between {start:%d %b %Y} and {end:%d %b %Y}.")
        left, right = st.columns(2)
        per_day = report_service.requests_per_day(report)
        left.plotly_chart(px.bar(per_day, x="Date", y="Requests", title="Requests created per day",
                                 color_discrete_sequence=GREEN), width="stretch")
        dist = report_service.status_distribution(report)
        right.plotly_chart(px.bar(dist, x="Status", y="Requests", title="Requests by current status",
                                  color="Status", color_discrete_sequence=GREEN), width="stretch")
        performance = report_service.delivery_performance(report)
        if performance.empty:
            st.caption("Delivery-time charts appear after the first completed delivery in this period.")
        else:
            st.plotly_chart(px.histogram(performance, x="Hours in transit", nbins=12,
                                         title="Time from dispatch to delivery (hours)",
                                         color_discrete_sequence=GREEN), width="stretch")
            with_eta = performance[performance["On time"] != "No ETA set"]
            if not with_eta.empty:
                rate = (with_eta["On time"] == "Yes").mean() * 100
                st.metric("Delivered by the estimated time", f"{rate:.0f}%",
                          help=f"Of {len(with_eta)} delivered request(s) that had an estimated delivery time.")
    st.subheader("Recent transport events")
    events = runtime.query(lambda s: tracking_service.recent_events(s, user, limit=12), []) or []
    timeline(history_events(events), tz)

with tab_users:
    f1, f2, f3 = st.columns([2, 1, 1])
    search = f1.text_input("Search name or email", key="user_search")
    role_filter = f2.selectbox("Role", [None, *domain.ALL_ROLES], format_func=lambda r: "All roles" if r is None
                               else domain.ROLE_LABELS[r])
    active_filter = f3.selectbox("Status", [None, True, False],
                                 format_func=lambda v: {None: "All", True: "Active", False: "Deactivated"}[v])
    users = runtime.query(lambda s: admin_service.list_users(s, user, search=search, role=role_filter,
                                                             active=active_filter), []) or []
    frame = pd.DataFrame([{
        "Name": u.full_name, "Email": u.email, "Phone": u.phone_number or "",
        "Roles": ", ".join(domain.ROLE_LABELS[r] for r in u.roles), "Active": "Yes" if u.is_active else "No",
        "Registered": format_datetime(u.created_at, tz), "Last sign-in": format_datetime(u.last_login_at, tz),
    } for u in users])
    if frame.empty:
        empty_state("No users match", "Try a different search or filter.")
    else:
        st.dataframe(frame, hide_index=True, width="stretch")
        csv_download(frame, filename="farmtrace_users.csv", label="Export users CSV")

        st.subheader("Manage a user")
        target = st.selectbox("User", users, format_func=lambda u: f"{u.full_name} <{u.email}>", key="manage_user")
        st.caption(f"Current roles: {', '.join(domain.ROLE_LABELS[r] for r in target.roles)} · "
                   f"{'Active' if target.is_active else 'Deactivated'}")
        a, b = st.columns(2)
        with a.form("grant_role"):
            new_role = st.selectbox("Grant role", [r for r in domain.ALL_ROLES if r not in target.roles],
                                    format_func=lambda r: domain.ROLE_LABELS[r])
            confirm = st.checkbox("I confirm this role change")
            if st.form_submit_button("Grant role"):
                if not confirm or new_role is None:
                    st.error("Tick the confirmation box and choose a role.")
                else:
                    runtime.act(lambda s: admin_service.grant_role(s, user, target.id, new_role),
                                success="Role granted.")
        with b.form("revoke_role"):
            old_role = st.selectbox("Remove role", list(target.roles), format_func=lambda r: domain.ROLE_LABELS[r])
            confirm = st.checkbox("I confirm removing this role")
            if st.form_submit_button("Remove role"):
                if not confirm:
                    st.error("Tick the confirmation box first.")
                else:
                    runtime.act(lambda s: admin_service.revoke_role(s, user, target.id, old_role),
                                success="Role removed.")
        c, d = st.columns(2)
        with c.form("toggle_active"):
            verb = "Deactivate" if target.is_active else "Reactivate"
            reason = st.text_input(f"Reason to {verb.lower()} this account")
            confirm = st.checkbox(f"I confirm: {verb.lower()} {target.email}")
            if st.form_submit_button(f"{verb} account", type="primary" if not target.is_active else "secondary"):
                if not confirm:
                    st.error("Tick the confirmation box first.")
                else:
                    runtime.act(lambda s: admin_service.set_user_active(s, user, target.id, not target.is_active,
                                                                        reason), success=f"Account {verb.lower()}d.")
            st.caption("Accounts are deactivated, never deleted, so delivery history stays intact.")
        with d.form("reset_password"):
            st.markdown("**Issue a temporary password**")
            confirm = st.checkbox("I have verified this person's identity")
            if st.form_submit_button("Generate temporary password"):
                if not confirm:
                    st.error("Confirm the person's identity first.")
                else:
                    temporary = runtime.act(lambda s: admin_service.reset_user_password(s, user, target.id),
                                            rerun=False)
                    if temporary:
                        st.success("Share this password privately. It is shown only once.")
                        st.code(temporary, language=None)

with tab_drivers:
    drivers = runtime.query(lambda s: driver_service.list_drivers(s, user), []) or []
    if not drivers:
        empty_state("No driver profiles yet", "Drivers appear here after registering with vehicle details.")
    else:
        loads = runtime.query(lambda s: driver_service.active_load(s, [d.id for d in drivers]), {}) or {}
        frame = pd.DataFrame([{
            "Driver": d.user.full_name, "Phone": d.user.profile.phone_number or "", "Vehicle": d.vehicle_type,
            "Registration": d.vehicle_registration_number, "Capacity (kg)": float(d.vehicle_capacity),
            "Licence": mask_identifier(d.driver_license_number),
            "Availability": domain.AVAILABILITY_LABELS[d.availability_status],
            "Active trips": loads.get(d.id, 0), "Account": "Active" if d.user.is_active else "Deactivated",
        } for d in drivers])
        searchable_table(frame, key="drivers_table", placeholder="Search drivers or vehicles…")
        st.caption("Licence numbers are masked in lists to limit exposure of personal data.")

with tab_audit:
    actions = runtime.query(lambda s: admin_service.audit_actions(s, user), []) or []
    chosen = st.selectbox("Action", [None, *actions], format_func=lambda a: "All actions" if a is None
                          else a.replace("_", " "))
    entries = runtime.query(lambda s: admin_service.list_audit_logs(s, user, action=chosen), []) or []
    frame = pd.DataFrame([{
        "Time": format_datetime(e.created_at, tz), "By": e.actor, "Action": e.action, "Target": e.target,
        "Details": e.details,
    } for e in entries])
    if frame.empty:
        empty_state("No audit entries", "Administrative actions will be recorded here.")
    else:
        st.dataframe(frame, hide_index=True, width="stretch")
        csv_download(frame, filename="farmtrace_audit_log.csv", label="Export audit log CSV")

with tab_integrity:
    st.write(
        "Each delivery's history is hash-linked: every event stores a SHA-256 fingerprint of its contents and of "
        "the previous event. Re-checking the chain reveals edited, reordered or deleted events."
    )
    st.caption(
        "This is tamper-evident, not tamper-proof: someone with full database-owner privileges could rewrite a "
        "whole chain consistently. Keeping exported history reports lets you compare hashes later."
    )
    if st.button("Verify all delivery histories", type="primary", icon=":material/verified:"):
        results = runtime.query(lambda s: tracking_service.verify_all_chains(s, user), []) or []
        broken = [r for r in results if not r.valid]
        if not results:
            st.info("There are no transport histories to check yet.")
        elif not broken:
            st.success(f"All {len(results)} delivery histories are intact "
                       f"({sum(r.events_checked for r in results)} events checked).")
        else:
            st.error(f"{len(broken)} of {len(results)} histories show signs of alteration.")
            for result in broken:
                with st.expander(result.request_number or str(result.transport_request_id)):
                    for problem in result.problems:
                        st.write("• " + problem)
