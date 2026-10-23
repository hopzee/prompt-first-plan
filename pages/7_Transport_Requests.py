"""Transport requests: farmers create and cancel; administrators approve, assign and manage."""

import datetime as dt

import streamlit as st

from farmtrace import domain
from farmtrace.components import guards, runtime
from farmtrace.components.dashboard_cards import empty_state, page_header
from farmtrace.components.deliveries import request_label, request_summary, requests_frame
from farmtrace.services import driver_service, farmer_service, transport_service
from farmtrace.services.transport_service import RequestFilters
from farmtrace.utils.helpers import NIGERIAN_STATES, format_quantity, local_datetime_to_utc, local_today

user = guards.require_access(domain.ROLE_FARMER, domain.ROLE_ADMIN)
settings = runtime.settings()
tz = settings.timezone
is_farmer = user.has_role(domain.ROLE_FARMER)

page_header("Transport requests", "Request, approve and assign transport for produce batches.")

tabs = st.tabs((["New request"] if is_farmer else []) + ["Requests"])

if is_farmer:
    with tabs[0]:
        eligible = runtime.query(lambda s: farmer_service.batches_eligible_for_transport(s, user), []) or []
        if not eligible:
            empty_state("No batches ready for transport",
                        "Register a produce batch first. Batches with an active request are not listed.")
            st.page_link("pages/6_Produce_Management.py", label="Register produce", icon=":material/add_box:")
        else:
            with st.form("new_request", clear_on_submit=True):
                batch = st.selectbox(
                    "Produce batch", eligible,
                    format_func=lambda b: f"{b.batch_number} — {b.produce_name}, "
                                          f"{format_quantity(b.quantity, b.unit_of_measurement)}",
                )
                pickup_date = st.date_input("Requested pickup date", value=local_today() + dt.timedelta(days=1),
                                            min_value=local_today(), max_value=local_today() + dt.timedelta(days=180))
                pickup = st.text_input("Pickup location (leave blank to use the batch's)")
                destination = st.text_input("Destination address (leave blank to use the batch's)")
                notes = st.text_area("Notes for the transporter (optional)", max_chars=1000,
                                     placeholder="e.g. Loading ramp available; call on arrival")
                if st.form_submit_button("Submit request", type="primary"):
                    runtime.act(lambda s: transport_service.create_request(
                        s, user, batch_id=batch.id, requested_pickup_date=pickup_date, pickup_location=pickup,
                        destination_location=destination, notes=notes),
                        success="Transport request submitted. An administrator will review it.")

with tabs[-1]:
    f1, f2, f3 = st.columns([2, 1, 1])
    search = f1.text_input("Search", placeholder="Request no., batch, produce, location, farm")
    status = f2.selectbox("Status", [None, *domain.DELIVERY_STATUSES], format_func=lambda v: v or "All")
    delayed = f3.toggle("Delayed only")
    g1, g2, g3 = st.columns(3)
    date_from = g1.date_input("Created from", value=None, key="req_from")
    date_to = g2.date_input("Created to", value=None, key="req_to")
    state = g3.selectbox("Farm state", [None, *NIGERIAN_STATES], format_func=lambda v: v or "All states")
    filters = RequestFilters(status=status, search=search, delayed_only=delayed, date_from=date_from,
                             date_to=date_to, state=state)
    requests = runtime.query(lambda s: transport_service.list_requests(s, user, filters, tz_name=tz), []) or []
    if not user.is_admin:
        requests = [r for r in requests if r.farmer_user_id == user.id]
    if not requests:
        empty_state("No requests found", "Transport requests matching your filters will appear here.")
        st.stop()
    st.dataframe(requests_frame(requests, tz), hide_index=True, width="stretch")

    st.subheader("Request details")
    request = st.selectbox("Select a request", requests, format_func=request_label)
    request_summary(request, tz, show_driver=True)
    rid = request.id
    st.page_link("pages/8_Delivery_Tracking.py", label="Open full tracking history", icon=":material/timeline:")

    # ---------------------------------------------------------------- farmer
    if not user.is_admin:
        if request.status in domain.FARMER_CANCELLABLE_STATUSES:
            with st.expander("Cancel this request"):
                with st.form("farmer_cancel"):
                    reason = st.text_input("Reason")
                    confirm = st.checkbox("I confirm I want to cancel this request")
                    if st.form_submit_button("Cancel request"):
                        if not confirm:
                            st.error("Tick the confirmation box first.")
                        else:
                            runtime.act(lambda s: transport_service.cancel_request(s, user, rid, reason),
                                        success="Request cancelled. You can request transport again later.")
        elif request.status in domain.ACTIVE_STATUSES:
            st.caption("A driver is assigned. Contact an administrator if this delivery must be cancelled.")
        st.stop()

    # ----------------------------------------------------------------- admin
    st.divider()
    st.markdown("#### Administrator actions")

    if request.status == domain.STATUS_PENDING:
        with st.form("approve"):
            c1, c2, c3 = st.columns(3)
            cost = c1.number_input("Agreed transport cost (₦)", min_value=0.0, value=0.0, step=1000.0)
            eta_day = c2.date_input("Estimated delivery date", value=None, min_value=local_today())
            eta_time = c3.time_input("Estimated delivery time", value=dt.time(17, 0))
            note = st.text_input("Note (optional)")
            if st.form_submit_button("Approve request", type="primary"):
                eta = local_datetime_to_utc(eta_day, eta_time, tz) if eta_day else None
                runtime.act(lambda s: transport_service.approve_request(
                    s, user, rid, transport_cost=cost if cost > 0 else None, estimated_delivery_time=eta, note=note),
                    success="Request approved.")

    if request.status in (domain.STATUS_APPROVED, domain.STATUS_ASSIGNED):
        drivers = runtime.query(lambda s: driver_service.list_drivers(s, user, eligible_only=True), []) or []
        loads = runtime.query(lambda s: driver_service.active_load(s, [d.id for d in drivers]), {}) or {}
        drivers = [d for d in drivers if d.id != request.assigned_driver_id]
        if not drivers:
            st.warning("No eligible drivers are available. Drivers must be active and not marked unavailable.")
        else:
            verb = "Assign" if request.status == domain.STATUS_APPROVED else "Reassign to"
            with st.form("assign"):
                chosen = st.selectbox(
                    f"{verb} driver", drivers,
                    format_func=lambda d: f"{d.user.full_name} · {d.vehicle_type} ({d.vehicle_registration_number}) · "
                                          f"{float(d.vehicle_capacity):,.0f} kg · "
                                          f"{domain.AVAILABILITY_LABELS[d.availability_status]} · "
                                          f"{loads.get(d.id, 0)} active trip(s)",
                )
                note = st.text_input("Note (optional)")
                if st.form_submit_button(f"{verb} driver", type="primary"):
                    runtime.act(lambda s: transport_service.assign_driver(s, user, rid, chosen.id, note=note),
                                success="Driver assignment saved.")

    if request.status in domain.ACTIVE_STATUSES and request.status != domain.STATUS_PENDING:
        with st.expander("Update cost or estimated delivery"):
            with st.form("logistics"):
                c1, c2, c3 = st.columns(3)
                cost = c1.number_input("Transport cost (₦)", min_value=0.0,
                                       value=float(request.transport_cost or 0), step=1000.0)
                eta_day = c2.date_input("Estimated delivery date", value=None, min_value=local_today(), key="eta2")
                eta_time = c3.time_input("Time", value=dt.time(17, 0), key="eta2_time")
                if st.form_submit_button("Save logistics"):
                    eta = local_datetime_to_utc(eta_day, eta_time, tz) if eta_day else None
                    runtime.act(lambda s: transport_service.update_logistics(
                        s, user, rid, transport_cost=cost if cost > 0 else None, estimated_delivery_time=eta),
                        success="Logistics updated.")

    if request.status in domain.DELAYABLE_STATUSES:
        with st.expander("Resolve delay" if request.is_delayed else "Report a delay"):
            with st.form("admin_delay"):
                text = st.text_input("Note" if request.is_delayed else "Delay reason")
                if st.form_submit_button("Resolve delay" if request.is_delayed else "Report delay"):
                    if request.is_delayed:
                        runtime.act(lambda s: transport_service.resolve_delay(s, user, rid, note=text),
                                    success="Delay resolved.")
                    else:
                        runtime.act(lambda s: transport_service.report_delay(s, user, rid, text),
                                    success="Delay reported.")

    if request.status in domain.ACTIVE_STATUSES:
        with st.expander("Cancel this request"):
            with st.form("admin_cancel"):
                reason = st.text_input("Reason")
                confirm = st.checkbox("I confirm this cancellation. It cannot be undone.")
                if st.form_submit_button("Cancel request"):
                    if not confirm:
                        st.error("Tick the confirmation box first.")
                    else:
                        runtime.act(lambda s: transport_service.cancel_request(s, user, rid, reason),
                                    success="Request cancelled.")
    if request.status in domain.ON_ROAD_STATUSES:
        st.caption("Only the assigned driver can record dispatch, transit and delivery.")
