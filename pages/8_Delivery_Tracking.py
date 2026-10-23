"""Chronological tracking history for one delivery, with a hash-chain integrity check."""

import pandas as pd
import streamlit as st

from farmtrace import domain
from farmtrace.components import guards, runtime
from farmtrace.components.dashboard_cards import empty_state, page_header, progress_steps, timeline
from farmtrace.components.deliveries import history_events, request_label, request_summary
from farmtrace.components.tables import csv_download
from farmtrace.services import tracking_service, transport_service
from farmtrace.services.transport_service import RequestFilters
from farmtrace.utils.helpers import format_datetime

user = guards.require_access(domain.ROLE_FARMER, domain.ROLE_DRIVER, domain.ROLE_ADMIN)
tz = runtime.settings().timezone

page_header("Delivery tracking", "Every recorded step of a delivery, oldest first.")

c1, c2 = st.columns([2, 1])
search = c1.text_input("Find a delivery", placeholder="Request no., batch no., produce…")
only_active = c2.toggle("Active only", value=False)
requests = runtime.query(lambda s: transport_service.list_requests(s, user, RequestFilters(search=search), tz_name=tz),
                         []) or []
if only_active:
    requests = [r for r in requests if r.status in domain.ACTIVE_STATUSES]
if not requests:
    empty_state("No deliveries to show", "Deliveries you are involved in will appear here.")
    st.stop()

request = st.selectbox("Delivery", requests, format_func=request_label)
progress_steps(request.status)

left, right = st.columns([2, 3])
with left:
    with st.container(border=True):
        request_summary(request, tz, show_driver=True)
    check = runtime.query(lambda s: tracking_service.verify_request_chain(s, user, request.id))
    if check is not None:
        if check.valid:
            st.success(f"History intact · {check.events_checked} hash-linked event(s) verified.",
                       icon=":material/verified:")
        else:
            st.error("This history fails its integrity check — events may have been altered.",
                     icon=":material/gpp_bad:")
            for problem in check.problems:
                st.caption("• " + problem)
with right:
    history = runtime.query(lambda s: tracking_service.get_request_history(s, user, request.id), []) or []
    timeline(history_events(history), tz)
    if history:
        frame = pd.DataFrame([{
            "Sequence": e.sequence_number, "Event": domain.EVENT_LABELS.get(e.event_type, e.event_type),
            "Status": e.new_status or "", "Description": e.event_description, "Location": e.recorded_location or "",
            "Recorded by": e.actor.full_name if e.actor else "System", "Time": format_datetime(e.event_timestamp, tz),
            "Hash": e.event_hash,
        } for e in history])
        csv_download(frame, filename=f"{request.request_number}_history.csv", label="Download this history (CSV)")
