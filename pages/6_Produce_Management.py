"""Produce batches: registration, search, QR labels and (admin) QR revocation."""

import datetime as dt

import pandas as pd
import streamlit as st

from farmtrace import domain
from farmtrace.components import guards, runtime
from farmtrace.components.dashboard_cards import (
    batch_badge,
    demo_badge,
    empty_state,
    key_values,
    page_header,
    show_html,
)
from farmtrace.errors import FarmTraceError
from farmtrace.services import farmer_service, qr_service
from farmtrace.services.farmer_service import BatchFilters, BatchInput
from farmtrace.utils.helpers import (
    NIGERIAN_STATES,
    PRODUCE_CATEGORIES,
    UNITS_OF_MEASUREMENT,
    format_date,
    format_datetime,
    format_quantity,
    local_today,
)

user = guards.require_access(domain.ROLE_FARMER, domain.ROLE_ADMIN)
settings = runtime.settings()
tz = settings.timezone
is_farmer = user.has_role(domain.ROLE_FARMER)

page_header("Produce batches", "Every batch gets a unique QR code that buyers can scan to verify it.")

tabs = st.tabs((["Register produce"] if is_farmer else []) + ["Batches"])

if is_farmer:
    with tabs[0]:
        farms = runtime.query(lambda s: farmer_service.list_farms(s, user, active_only=True), []) or []
        farms = [f for f in farms if f.farmer_user_id == user.id]
        if not farms:
            empty_state("Register a farm first", "Add a farm on your Farmer dashboard, then come back here.")
            st.page_link("pages/3_Farmer_Dashboard.py", label="Go to Farmer dashboard", icon=":material/agriculture:")
        else:
            with st.form("new_batch", clear_on_submit=True):
                farm = st.selectbox("Farm", farms, format_func=lambda f: f"{f.farm_name} ({f.state})")
                c1, c2 = st.columns(2)
                produce = c1.text_input("Produce name", placeholder="e.g. Tomatoes, Cassava, Yam")
                category = c2.selectbox("Category", PRODUCE_CATEGORIES)
                quantity = c1.number_input("Quantity", min_value=0.01, value=10.0, step=1.0)
                unit = c2.selectbox("Unit of measurement", UNITS_OF_MEASUREMENT)
                harvest = c1.date_input("Harvest date", value=local_today(), max_value=local_today(),
                                        min_value=local_today() - dt.timedelta(days=366))
                pickup = c2.text_input("Pickup location", placeholder="e.g. Farm gate, Ogbomoso")
                market = c1.text_input("Destination market", placeholder="e.g. Mile 12 Market")
                destination = c2.text_input("Destination address", placeholder="e.g. Mile 12, Kosofe, Lagos")
                notes = st.text_area("Additional notes (private)", max_chars=2000,
                                     help="Visible to you and administrators only.")
                if st.form_submit_button("Register batch", type="primary"):
                    data = BatchInput(farm.id, produce, category, quantity, unit, harvest, pickup, market,
                                      destination, notes)
                    runtime.act(lambda s: farmer_service.create_batch(s, user, data),
                                success="Batch registered with a new QR code.")

with tabs[-1]:
    f1, f2, f3, f4 = st.columns(4)
    search = f1.text_input("Search", placeholder="Batch no., produce, market, farm")
    status = f2.selectbox("Batch status", [None, *domain.BATCH_STATUSES], format_func=lambda v: v or "All")
    category = f3.selectbox("Category", [None, *PRODUCE_CATEGORIES], format_func=lambda v: v or "All")
    state = f4.selectbox("State", [None, *NIGERIAN_STATES], format_func=lambda v: v or "All states")
    filters = BatchFilters(search=search, status=status, category=category, state=state)
    batches = runtime.query(lambda s: farmer_service.list_batches(s, user, filters), []) or []

    if not batches:
        empty_state("No batches found", "Registered produce batches will be listed here.")
        st.stop()

    rows = [{
        "Batch": b.batch_number, "Produce": b.produce_name, "Category": b.produce_category,
        "Quantity": format_quantity(b.quantity, b.unit_of_measurement), "Harvested": b.harvest_date,
        "Farm": b.farm.farm_name, "State": b.farm.state, "Status": b.batch_status,
        "QR": "Revoked" if b.qr_revoked_at else "Active", "Demo": "Yes" if b.is_demo else "",
    } for b in batches]
    if user.is_admin:
        for row, b in zip(rows, batches):
            row["Farmer"] = b.farmer.full_name if b.farmer else ""
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Batch details & QR label")
    batch = st.selectbox("Select a batch", batches,
                         format_func=lambda b: f"{b.batch_number} — {b.produce_name} ({b.batch_status})")
    info, qr_col = st.columns([3, 2])
    with info:
        show_html(batch_badge(batch.batch_status) + demo_badge(batch.is_demo))
        key_values([
            ("Batch number", batch.batch_number),
            ("Produce", f"{batch.produce_name} ({batch.produce_category})"),
            ("Quantity", format_quantity(batch.quantity, batch.unit_of_measurement)),
            ("Harvest date", format_date(batch.harvest_date)),
            ("Farm", f"{batch.farm.farm_name}, {batch.farm.local_government_area}, {batch.farm.state}"),
            ("Pickup location", batch.pickup_location),
            ("Destination", f"{batch.destination_market} — {batch.destination_location}"),
            ("Registered", format_datetime(batch.created_at, tz)),
            ("Notes", batch.additional_notes),
        ])
    with qr_col:
        if batch.qr_revoked_at:
            st.error("This QR code has been revoked. An administrator can issue a replacement.")
        elif not settings.public_app_url:
            st.warning(
                "QR codes are disabled until **PUBLIC_APP_URL** is set to this app's public address "
                "(for example https://your-app.streamlit.app). This prevents printing labels that lead nowhere."
            )
        else:
            try:
                bundle = runtime.query(lambda s: qr_service.get_batch_qr(s, user, batch.id, settings.public_app_url))
            except FarmTraceError as exc:  # pragma: no cover - query() already reports errors
                bundle = None
                st.error(exc.user_message)
            if bundle:
                st.image(bundle.label_png, caption="Printable packaging label", width=280)
                d1, d2 = st.columns(2)
                d1.download_button("QR code (PNG)", bundle.png, file_name=f"{batch.batch_number}_qr.png",
                                   mime="image/png", icon=":material/qr_code:", on_click="ignore", width="stretch")
                d2.download_button("Print label (PNG)", bundle.label_png, file_name=f"{batch.batch_number}_label.png",
                                   mime="image/png", icon=":material/print:", on_click="ignore", width="stretch")
                st.caption("Scanning opens:")
                st.code(bundle.url, language=None)

    owner = batch.farmer_user_id == user.id
    if owner and batch.batch_status == domain.BATCH_REGISTERED:
        with st.expander("Edit pickup and destination"):
            with st.form("edit_batch"):
                pickup = st.text_input("Pickup location", value=batch.pickup_location)
                market = st.text_input("Destination market", value=batch.destination_market)
                destination = st.text_input("Destination address", value=batch.destination_location)
                notes = st.text_area("Additional notes", value=batch.additional_notes or "")
                if st.form_submit_button("Save changes"):
                    runtime.act(lambda s: farmer_service.update_batch_details(
                        s, user, batch.id, pickup_location=pickup, destination_market=market,
                        destination_location=destination, additional_notes=notes), success="Batch updated.")
    if (owner or user.is_admin) and batch.batch_status in (domain.BATCH_REGISTERED,):
        with st.expander("Withdraw this batch"):
            with st.form("cancel_batch"):
                reason = st.text_input("Reason", placeholder="e.g. Produce spoiled by rain")
                confirm = st.checkbox("I understand the QR code will show this batch as withdrawn.")
                if st.form_submit_button("Withdraw batch"):
                    if not confirm:
                        st.error("Tick the confirmation box first.")
                    else:
                        runtime.act(lambda s: farmer_service.cancel_batch(s, user, batch.id, reason),
                                    success="Batch withdrawn.")
    if user.is_admin:
        with st.expander("QR code administration"):
            with st.form("qr_admin"):
                action = st.radio("Action", ["Revoke current QR code", "Issue a replacement QR code"],
                                  help="Revoke if labels were copied or misused. A replacement invalidates old labels.")
                reason = st.text_input("Reason")
                confirm = st.checkbox("I confirm this QR change")
                if st.form_submit_button("Apply"):
                    if not confirm:
                        st.error("Tick the confirmation box first.")
                    elif action.startswith("Revoke"):
                        runtime.act(lambda s: qr_service.revoke_qr(s, user, batch.id, reason), success="QR code revoked.")
                    else:
                        runtime.act(lambda s: qr_service.reissue_qr(s, user, batch.id, reason),
                                    success="Replacement QR code issued. Old labels now show as withdrawn.")
