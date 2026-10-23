"""Driver dashboard: vehicle profile, availability, assigned deliveries and open requests."""

import streamlit as st

from farmtrace import domain
from farmtrace.authentication.auth import DriverDetails
from farmtrace.components import guards, runtime
from farmtrace.components.dashboard_cards import empty_state, metric_cards, page_header, show_html, status_badge
from farmtrace.components.deliveries import driver_controls, request_summary, requests_frame
from farmtrace.services import driver_service, transport_service
from farmtrace.utils.helpers import VEHICLE_TYPES, format_date, format_quantity

user = guards.require_access(domain.ROLE_DRIVER)
settings = runtime.settings()
tz = settings.timezone

page_header(f"Welcome, {user.full_name.split()[0]}", "Your trips, in the order you need to do them.")


def vehicle_form(key: str, driver=None) -> DriverDetails | None:
    with st.form(key):
        c1, c2 = st.columns(2)
        licence = c1.text_input("Driver's licence number", value=driver.driver_license_number if driver else "")
        plate = c2.text_input("Vehicle registration number", value=driver.vehicle_registration_number if driver else "")
        vehicle_type = c1.selectbox(
            "Vehicle type", VEHICLE_TYPES,
            index=VEHICLE_TYPES.index(driver.vehicle_type) if driver and driver.vehicle_type in VEHICLE_TYPES else 0,
        )
        capacity = c2.number_input("Load capacity (kg)", min_value=1.0, max_value=100000.0,
                                   value=float(driver.vehicle_capacity) if driver else 1000.0, step=100.0)
        if st.form_submit_button("Save vehicle profile", type="primary"):
            return DriverDetails(licence, plate, vehicle_type, capacity)
    return None


profile = runtime.query(lambda s: driver_service.get_own_profile(s, user))
if profile is None:
    st.warning("Add your licence and vehicle details before you can receive deliveries.")
    details = vehicle_form("create_vehicle")
    if details:
        runtime.act(lambda s: driver_service.create_driver_profile(s, user, details), success="Vehicle profile saved.")
    st.stop()

summary = runtime.query(lambda s: driver_service.driver_summary(s, user), {}) or {}
metric_cards([
    ("Active trips", summary.get("active", 0), "Assigned, dispatched or in transit"),
    ("On the road", summary.get("on_road", 0), None),
    ("Delivered", summary.get("delivered", 0), None),
    ("Open delays", summary.get("delayed", 0), None),
])

with st.container(border=True):
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    availability = c1.selectbox(
        "My availability", domain.AVAILABILITY_STATUSES,
        index=domain.AVAILABILITY_STATUSES.index(profile.availability_status),
        format_func=lambda a: domain.AVAILABILITY_LABELS[a],
        help="Administrators only assign new trips to drivers who are not marked unavailable.",
    )
    if c2.button("Update", width="stretch", disabled=availability == profile.availability_status):
        runtime.act(lambda s: driver_service.set_availability(s, user, availability), success="Availability updated.")

tab_active, tab_open, tab_done, tab_vehicle = st.tabs(
    ["My active deliveries", f"Open requests ({summary.get('open_requests', 0)})", "Completed", "Vehicle profile"]
)

with tab_active:
    active = runtime.query(lambda s: transport_service.list_assigned_to_me(s, user), []) or []
    if not active:
        empty_state("No active deliveries", "New assignments will appear here.")
    for request in active:
        with st.container(border=True):
            st.markdown(f"#### {request.batch.produce_name} → {request.batch.destination_market}")
            request_summary(request, tz, show_driver=False, show_farm_contact=True)
            st.divider()
            driver_controls(user, request)

with tab_open:
    if not settings.allow_driver_self_assign:
        st.info("In this deployment an administrator assigns deliveries to drivers.")
    else:
        open_requests = runtime.query(lambda s: transport_service.list_open_requests_for_drivers(s, user), []) or []
        if not open_requests:
            empty_state("No open requests", "Approved requests that still need a driver will show here.")
        for request in open_requests:
            with st.container(border=True):
                batch = request.batch
                st.markdown(f"**{batch.produce_name}** · {format_quantity(batch.quantity, batch.unit_of_measurement)}")
                st.caption(
                    f"{request.pickup_location} ({batch.farm.state}) → {request.destination_location} · "
                    f"pickup {format_date(request.requested_pickup_date)}"
                )
                if st.button("Accept this delivery", key=f"accept_{request.id}", type="primary"):
                    rid = request.id
                    runtime.act(
                        lambda s: transport_service.accept_request(
                            s, user, rid, self_assign_enabled=settings.allow_driver_self_assign),
                        success=f"You accepted {request.request_number}.",
                    )

with tab_done:
    finished = runtime.query(lambda s: transport_service.list_assigned_to_me(s, user, active_only=False), []) or []
    if not finished:
        empty_state("No completed trips yet", "Delivered and cancelled trips will be listed here.")
    else:
        st.dataframe(requests_frame(finished, tz), hide_index=True, width="stretch")

with tab_vehicle:
    show_html(status_badge(domain.AVAILABILITY_LABELS[profile.availability_status]))
    details = vehicle_form("edit_vehicle", profile)
    if details:
        runtime.act(lambda s: driver_service.update_driver_profile(s, user, details), success="Vehicle profile updated.")
