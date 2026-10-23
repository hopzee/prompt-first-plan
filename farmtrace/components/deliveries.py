"""Shared transport-request views and the driver's status-update controls.

The controls only collect input; every change is performed (and authorised) by
``transport_service`` so all pages follow the same workflow rules.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd
import streamlit as st

from farmtrace import domain
from farmtrace.authentication.permissions import CurrentUser
from farmtrace.components import runtime
from farmtrace.components.dashboard_cards import demo_badge, key_values, show_html, status_badge
from farmtrace.database.models import TransportHistory, TransportRequest
from farmtrace.services import transport_service
from farmtrace.utils.helpers import format_date, format_datetime, format_naira, format_quantity


def request_label(request: TransportRequest) -> str:
    flag = " · Delayed" if request.is_delayed else ""
    return f"{request.request_number} — {request.batch.produce_name} ({request.status}{flag})"


def requests_frame(requests: Sequence[TransportRequest], tz_name: str, *, include_farm: bool = True) -> pd.DataFrame:
    rows = []
    for r in requests:
        row = {
            "Request": r.request_number,
            "Produce": r.batch.produce_name,
            "Quantity": format_quantity(r.batch.quantity, r.batch.unit_of_measurement),
            "Status": r.display_status,
            "Pickup date": r.requested_pickup_date,
            "From": r.pickup_location,
            "To": r.destination_location,
            "Driver": r.driver.user.full_name if r.driver else "—",
            "Updated": format_datetime(r.updated_at, tz_name),
            "Demo": "Yes" if r.is_demo else "",
        }
        if include_farm:
            row["Farm"] = r.batch.farm.farm_name
        rows.append(row)
    return pd.DataFrame(rows)


def request_summary(request: TransportRequest, tz_name: str, *, show_driver: bool = True,
                    show_farm_contact: bool = False) -> None:
    batch = request.batch
    show_html(status_badge(request.status, request.is_delayed) + demo_badge(request.is_demo))
    pairs = [
        ("Request", request.request_number),
        ("Batch", batch.batch_number),
        ("Produce", f"{batch.produce_name} · {format_quantity(batch.quantity, batch.unit_of_measurement)}"),
        ("Farm", f"{batch.farm.farm_name}, {batch.farm.local_government_area}, {batch.farm.state}"),
        ("Pickup", request.pickup_location),
        ("Destination", f"{batch.destination_market} — {request.destination_location}"),
        ("Requested pickup date", format_date(request.requested_pickup_date)),
        ("Estimated delivery", format_datetime(request.estimated_delivery_time, tz_name)),
        ("Dispatched", format_datetime(request.actual_dispatch_time, tz_name)),
        ("Delivered", format_datetime(request.actual_delivery_time, tz_name)),
        ("Transport cost", format_naira(request.transport_cost)),
    ]
    if show_farm_contact and batch.farm.contact_information:
        pairs.append(("Farm contact", batch.farm.contact_information))
    if show_driver:
        driver = request.driver
        pairs.append(("Driver", f"{driver.user.full_name} · {driver.vehicle_type} ({driver.vehicle_registration_number})"
                      if driver else "Not assigned yet"))
    if request.is_delayed:
        pairs.append(("Delay reason", request.delay_reason))
    if request.cancellation_reason:
        pairs.append(("Cancellation reason", request.cancellation_reason))
    key_values(pairs)
    if request.delivery_notes:
        with st.expander("Notes"):
            st.text(request.delivery_notes)


def history_events(history: Sequence[TransportHistory]) -> list[dict]:
    return [
        {
            "title": domain.EVENT_LABELS.get(e.event_type, e.event_type),
            "timestamp": e.event_timestamp,
            "status": e.new_status,
            "actor": e.actor.full_name if e.actor else None,
            "location": e.recorded_location,
            "body": e.event_description,
            "warn": e.event_type in (domain.EVENT_DELAY_REPORTED, domain.EVENT_CANCELLED),
        }
        for e in history
    ]


def driver_controls(user: CurrentUser, request: TransportRequest) -> None:
    """Status update, delay and checkpoint forms for the assigned driver."""
    next_status = transport_service.next_driver_status(request)
    rid = request.id
    if next_status:
        with st.form(f"advance_{rid}", clear_on_submit=True):
            st.markdown(f"**Next step: mark as {next_status}**")
            location = st.text_input("Current location (typed by you — no GPS)", key=f"loc_{rid}",
                                     placeholder="e.g. Ogbomoso farm gate")
            notes = st.text_input("Delivery notes (optional)", key=f"note_{rid}")
            confirm = st.checkbox(f"I confirm the produce is now {next_status}.", key=f"confirm_{rid}")
            if st.form_submit_button(f"Mark as {next_status}", type="primary"):
                if not confirm:
                    st.error("Tick the confirmation box first.")
                else:
                    runtime.act(
                        lambda s: transport_service.advance_delivery(s, user, rid, next_status, location=location,
                                                                     notes=notes),
                        success=f"{request.request_number} marked as {next_status}.",
                    )
    cols = st.columns(2)
    with cols[0]:
        if request.is_delayed:
            with st.form(f"resolve_{rid}", clear_on_submit=True):
                st.markdown("**Resolve delay**")
                note = st.text_input("What changed? (optional)", key=f"resolve_note_{rid}")
                if st.form_submit_button("Delay resolved"):
                    runtime.act(lambda s: transport_service.resolve_delay(s, user, rid, note=note),
                                success="Delay resolved; the delivery continues from its current stage.")
        elif request.status in domain.DELAYABLE_STATUSES:
            with st.form(f"delay_{rid}", clear_on_submit=True):
                st.markdown("**Report a delay**")
                reason = st.text_input("Reason", key=f"delay_reason_{rid}", placeholder="e.g. Heavy traffic at Berger")
                where = st.text_input("Current location (optional)", key=f"delay_loc_{rid}")
                if st.form_submit_button("Report delay"):
                    runtime.act(lambda s: transport_service.report_delay(s, user, rid, reason, location=where),
                                success="Delay reported. The farmer and administrators can see it.")
    with cols[1]:
        if request.status in domain.ON_ROAD_STATUSES:
            with st.form(f"checkpoint_{rid}", clear_on_submit=True):
                st.markdown("**Record a checkpoint**")
                where = st.text_input("Location", key=f"cp_loc_{rid}", placeholder="e.g. Oyo toll gate")
                note = st.text_input("Note (optional)", key=f"cp_note_{rid}")
                if st.form_submit_button("Save checkpoint"):
                    runtime.act(lambda s: transport_service.record_checkpoint(s, user, rid, location=where, note=note),
                                success="Checkpoint recorded.")
