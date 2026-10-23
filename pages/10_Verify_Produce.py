"""Public produce verification — no sign-in required.

Scanning a FarmTrace QR code opens ``/?verify=<token>``; the token is checked
against the database and only allow-listed public fields are shown.
"""

from urllib.parse import parse_qs, urlsplit

import streamlit as st

from farmtrace.components import runtime
from farmtrace.components.dashboard_cards import (
    batch_badge,
    demo_badge,
    key_values,
    page_header,
    show_html,
    status_badge,
    timeline,
)
from farmtrace.services import qr_service
from farmtrace.utils.helpers import format_date, format_datetime

tz = runtime.settings().timezone
page_header("Verify produce", "Scan the QR code on the packaging, or paste the link or code below.")


def extract_token(value: str) -> str:
    value = (value or "").strip()
    if "verify=" in value:
        return parse_qs(urlsplit(value).query).get("verify", [""])[0]
    return value


token = st.query_params.get("verify", "")
with st.form("verify_form"):
    entered = st.text_input("Verification link or code", value=token, placeholder="https://…/?verify=…")
    if st.form_submit_button("Verify", type="primary", icon=":material/search:"):
        token = extract_token(entered)
        if token:
            st.query_params["verify"] = token
        else:
            st.query_params.clear()

if not token:
    st.info("FarmTrace labels carry a QR code. Point your phone camera at it to open this page automatically.")
    st.stop()

result = runtime.query(lambda s: qr_service.verify_token(s, token))
if result is None:
    st.stop()

if result.status == "invalid":
    st.warning(result.message, icon=":material/help:")
    st.stop()
if result.status in ("unknown", "revoked"):
    st.error(result.message, icon=":material/gpp_bad:")
    st.caption("If you bought this produce, tell the seller. Do not rely on this label.")
    st.stop()

view = result.view
st.success(result.message, icon=":material/verified:")
if view.is_demo:
    st.warning("This is a **demonstration record** created for testing, not a real consignment.")
if result.notice:
    st.warning(result.notice)

left, right = st.columns([2, 3])
with left:
    with st.container(border=True):
        st.markdown(f"### {view.produce_name}")
        show_html(batch_badge(view.batch_status) + " " + status_badge(view.delivery_status, view.is_delayed)
                  + demo_badge(view.is_demo))
        key_values([
            ("Batch reference", view.batch_number),
            ("Category", view.produce_category),
            ("Quantity", view.quantity),
            ("Harvested", format_date(view.harvest_date)),
            ("Origin farm", view.origin_farm),
            ("Origin", f"{view.origin_lga}, {view.origin_state} State"),
            ("Destination market", view.destination_market),
            ("Dispatched", format_datetime(view.dispatch_time, tz)),
            ("Delivered", format_datetime(view.delivery_time, tz)),
        ])
with right:
    st.markdown("#### Journey")
    timeline(
        [{"title": e.label, "timestamp": e.timestamp, "status": e.status, "location": e.location,
          "warn": e.label in ("Delay reported", "Cancelled")} for e in view.events],
        tz,
    )

st.caption(
    "This page shows only public batch information. Farmer and driver contact details, licence numbers and "
    "internal records are never displayed. Locations are entered manually by drivers (no GPS)."
)
