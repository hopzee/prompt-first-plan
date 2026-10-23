"""Home page: what FarmTrace does and where to go next. Shows no invented statistics."""

from __future__ import annotations

import streamlit as st

from farmtrace import APP_TITLE, domain
from farmtrace.components import runtime
from farmtrace.components.dashboard_cards import show_html


def render() -> None:
    user = runtime.current_user()
    show_html(
        "<div class='ft-hero'><div class='ft-eyebrow'>FarmTrace · Nigeria</div>"
        "<h1>From farm gate to market, every trip on record.</h1>"
        f"<p>{APP_TITLE}. Farmers request transport, administrators assign verified drivers, drivers log each "
        "stage of the journey, and buyers scan a QR code to see where their produce came from.</p></div>"
    )

    actions = st.columns(3)
    if user is None:
        actions[0].page_link("pages/1_Login.py", label="Sign in", icon=":material/login:", width="stretch")
        actions[1].page_link("pages/2_Register.py", label="Create an account", icon=":material/person_add:",
                             width="stretch")
    else:
        target = {
            domain.ROLE_ADMIN: "pages/5_Admin_Dashboard.py",
            domain.ROLE_FARMER: "pages/3_Farmer_Dashboard.py",
            domain.ROLE_DRIVER: "pages/4_Driver_Dashboard.py",
        }.get(user.primary_role)
        if target:
            actions[0].page_link(target, label="Open my dashboard", icon=":material/dashboard:", width="stretch")
        actions[1].page_link("pages/1_Login.py", label="My account", icon=":material/person:", width="stretch")
    actions[2].page_link("pages/10_Verify_Produce.py", label="Verify produce", icon=":material/qr_code_scanner:",
                         width="stretch")

    st.subheader("Who it's for")
    cols = st.columns(3)
    blocks = (
        ("🧑🏾‍🌾 Farmers", "Register farms and produce batches, request transport, print QR labels and follow "
         "every delivery."),
        ("🚚 Drivers", "See assigned trips, record dispatch and delivery times, report delays and log checkpoints."),
        ("🛒 Buyers & consumers", "Scan the QR code on the packaging to confirm the batch is genuine and see its "
         "journey — no account needed."),
    )
    for column, (title, text) in zip(cols, blocks):
        with column.container(border=True):
            st.markdown(f"**{title}**")
            st.write(text)

    st.subheader("How a delivery moves")
    st.markdown(
        "1. **Pending** – the farmer requests transport for a registered batch.\n"
        "2. **Approved** – an administrator reviews the request and agrees the cost.\n"
        "3. **Assigned** – an available driver is assigned (or accepts the job).\n"
        "4. **Dispatched → In-Transit** – the driver records pickup and progress.\n"
        "5. **Delivered** – the driver confirms arrival. Delays are flagged without losing the current stage.\n\n"
        "Every step is stored with the time and the person responsible, in a hash-linked history that "
        "reveals later edits."
    )
    st.caption("Locations are typed in by drivers; FarmTrace does not use GPS tracking.")
