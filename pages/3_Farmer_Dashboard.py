"""Farmer dashboard: farms, summary statistics and recent transport activity."""

import pandas as pd
import streamlit as st

from farmtrace import domain
from farmtrace.components import guards, runtime
from farmtrace.components.dashboard_cards import empty_state, metric_cards, page_header
from farmtrace.components.deliveries import requests_frame
from farmtrace.services import farmer_service, transport_service
from farmtrace.services.farmer_service import FarmInput
from farmtrace.utils.helpers import NIGERIAN_STATES

user = guards.require_access(domain.ROLE_FARMER)
settings = runtime.settings()
tz = settings.timezone

page_header(f"Welcome, {user.full_name.split()[0]}", "Your farms, produce and deliveries at a glance.")

summary = runtime.query(lambda s: farmer_service.farmer_summary(s, user), {}) or {}
metric_cards([
    ("Farms", summary.get("farms", 0), None),
    ("Produce batches", summary.get("batches", 0), None),
    ("Awaiting a driver", summary.get("pending", 0), "Pending or approved requests"),
    ("On the way", summary.get("active", 0), "Assigned, dispatched or in transit"),
    ("Delivered", summary.get("delivered", 0), None),
    ("Delayed now", summary.get("delayed", 0), "Active deliveries with an open delay"),
], per_row=3)

links = st.columns(3)
links[0].page_link("pages/6_Produce_Management.py", label="Register produce", icon=":material/add_box:")
links[1].page_link("pages/7_Transport_Requests.py", label="Request transport", icon=":material/local_shipping:")
links[2].page_link("pages/8_Delivery_Tracking.py", label="Track deliveries", icon=":material/timeline:")


def farm_form(prefix: str, farm=None) -> FarmInput | None:
    with st.form(f"{prefix}_farm_form", clear_on_submit=farm is None):
        name = st.text_input("Farm name", value=farm.farm_name if farm else "", max_chars=150)
        location = st.text_input("Farm location / address", value=farm.farm_location if farm else "",
                                 placeholder="e.g. Km 5 Ogbomoso–Ilorin Road")
        c1, c2 = st.columns(2)
        state_index = NIGERIAN_STATES.index(farm.state) if farm else NIGERIAN_STATES.index("Oyo")
        state = c1.selectbox("State", NIGERIAN_STATES, index=state_index)
        lga = c2.text_input("Local government area", value=farm.local_government_area if farm else "")
        contact = st.text_input("Contact information for pickups (optional)",
                                value=(farm.contact_information or "") if farm else "",
                                help="Shown only to the driver assigned to your delivery — never on public pages.")
        if st.form_submit_button("Save farm" if farm else "Register farm", type="primary"):
            return FarmInput(name, location, state, lga, contact)
    return None


tab_farms, tab_new, tab_transport = st.tabs(["My farms", "Register a farm", "Recent transport"])

with tab_farms:
    farms = runtime.query(lambda s: farmer_service.list_farms(s, user), []) or []
    if not farms:
        empty_state("No farms yet", "Register your first farm in the next tab to start adding produce.")
    else:
        st.dataframe(pd.DataFrame([{
            "Farm": f.farm_name, "Location": f.farm_location, "LGA": f.local_government_area, "State": f.state,
            "Status": "Active" if f.is_active else "Inactive", "Demo": "Yes" if f.is_demo else "",
        } for f in farms]), hide_index=True, width="stretch")
        with st.expander("Edit a farm"):
            chosen = st.selectbox("Farm", farms, format_func=lambda f: f.farm_name, key="edit_farm_select")
            data = farm_form("edit", chosen)
            if data:
                runtime.act(lambda s: farmer_service.update_farm(s, user, chosen.id, data), success="Farm updated.")
            label = "Mark farm inactive" if chosen.is_active else "Reactivate farm"
            if st.button(label, key="toggle_farm"):
                runtime.act(lambda s: farmer_service.set_farm_active(s, user, chosen.id, not chosen.is_active),
                            success="Farm status updated.")

with tab_new:
    data = farm_form("new")
    if data:
        runtime.act(lambda s: farmer_service.create_farm(s, user, data), success="Farm registered.")

with tab_transport:
    requests = runtime.query(lambda s: transport_service.list_requests(s, user), []) or []
    mine = [r for r in requests if r.farmer_user_id == user.id]
    if not mine:
        empty_state("No transport requests yet", "Register produce, then request transport for it.")
    else:
        active = [r for r in mine if r.status in domain.ACTIVE_STATUSES]
        done = [r for r in mine if r.status in domain.TERMINAL_STATUSES]
        st.markdown(f"**Active requests ({len(active)})**")
        if active:
            st.dataframe(requests_frame(active, tz), hide_index=True, width="stretch")
        else:
            st.caption("Nothing in progress right now.")
        st.markdown(f"**Delivery history ({len(done)})**")
        if done:
            st.dataframe(requests_frame(done, tz), hide_index=True, width="stretch")
        else:
            st.caption("Completed and cancelled requests will appear here.")
