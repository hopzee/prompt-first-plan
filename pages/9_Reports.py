"""Filterable reports with CSV export and simple analytics (real database records only)."""

import datetime as dt

import plotly.express as px
import streamlit as st

from farmtrace import domain
from farmtrace.components import guards, runtime
from farmtrace.components.dashboard_cards import empty_state, page_header
from farmtrace.components.tables import csv_download, searchable_table
from farmtrace.services import report_service
from farmtrace.services.farmer_service import BatchFilters
from farmtrace.services.transport_service import RequestFilters
from farmtrace.utils.helpers import NIGERIAN_STATES, local_today

user = guards.require_access(domain.ROLE_FARMER, domain.ROLE_DRIVER, domain.ROLE_ADMIN)
tz = runtime.settings().timezone
can_see_batches = user.has_role(domain.ROLE_FARMER, domain.ROLE_ADMIN)
scope = "all records" if user.is_admin else "records you are involved in"

page_header("Reports", f"Filter and export {scope} as CSV (opens in Excel or Google Sheets).")

with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    date_from = c1.date_input("Created from", value=local_today() - dt.timedelta(days=90), key="rep_from")
    date_to = c2.date_input("Created to", value=local_today(), key="rep_to")
    status = c3.selectbox("Delivery status", [None, *domain.DELIVERY_STATUSES], format_func=lambda v: v or "All")
    d1, d2, d3 = st.columns(3)
    state = d1.selectbox("Farm state", [None, *NIGERIAN_STATES], format_func=lambda v: v or "All states")
    delayed = d2.toggle("Delayed only")
    include_demo = d3.toggle("Include demo records", value=True)

filters = RequestFilters(status=status, date_from=date_from, date_to=date_to, delayed_only=delayed, state=state,
                         include_demo=include_demo)
transport = runtime.query(lambda s: report_service.transport_report(s, user, filters, tz_name=tz))
stamp = local_today().strftime("%Y%m%d")

tab_names = ["Transport", "Tracking history", "Analytics"] + (["Produce batches"] if can_see_batches else [])
tabs = st.tabs(tab_names)

with tabs[0]:
    if transport is None or transport.empty:
        empty_state("No transport records", "Nothing matches these filters yet.")
    else:
        shown = searchable_table(transport, key="transport_report")
        csv_download(shown, filename=f"farmtrace_transport_{stamp}.csv", label="Export transport report")

with tabs[1]:
    history = runtime.query(lambda s: report_service.history_report(s, user, filters, tz_name=tz))
    if history is None or history.empty:
        empty_state("No tracking events", "Tracking events for the filtered requests will appear here.")
    else:
        shown = searchable_table(history, key="history_report")
        csv_download(shown, filename=f"farmtrace_history_{stamp}.csv", label="Export tracking history")
        st.caption("The Event hash column lets you re-check later that no recorded event has been changed.")

with tabs[2]:
    if transport is None or transport.empty:
        empty_state("No data to analyse", "Charts are drawn only from real records in the selected range.")
    else:
        st.caption(f"Based on {len(transport)} request(s) in the selected range.")
        a, b = st.columns(2)
        a.plotly_chart(px.bar(report_service.requests_per_day(transport), x="Date", y="Requests",
                              title="Requests created per day", color_discrete_sequence=["#2E7D32"]), width="stretch")
        b.plotly_chart(px.pie(report_service.status_distribution(transport), names="Status", values="Requests",
                              title="Current status", hole=0.45,
                              color_discrete_sequence=["#6A7365", "#1D4E89", "#00594F", "#E8A33D", "#C9A227",
                                                       "#2E7D32", "#9A2A1E"]), width="stretch")
        performance = report_service.delivery_performance(transport)
        if performance.empty:
            st.caption("Delivery-time analysis appears after deliveries are completed.")
        else:
            st.markdown("**Completed deliveries**")
            st.dataframe(performance, hide_index=True, width="stretch")
            csv_download(performance, filename=f"farmtrace_performance_{stamp}.csv", label="Export performance")

if can_see_batches:
    with tabs[3]:
        batches = runtime.query(lambda s: report_service.batch_report(s, user, BatchFilters(state=state), tz_name=tz))
        if batches is None or batches.empty:
            empty_state("No produce batches", "Registered batches will appear here.")
        else:
            if not include_demo:
                batches = batches[batches["Demo record"] == "No"]
            shown = searchable_table(batches, key="batch_report")
            csv_download(shown, filename=f"farmtrace_batches_{stamp}.csv", label="Export produce batches")
