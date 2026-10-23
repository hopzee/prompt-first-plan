"""Public registration for farmers, drivers and consumers (never administrators)."""

import streamlit as st

from farmtrace import domain
from farmtrace.authentication import auth
from farmtrace.authentication.auth import DriverDetails, RegistrationData
from farmtrace.components import runtime
from farmtrace.components.dashboard_cards import page_header
from farmtrace.utils.helpers import VEHICLE_TYPES

if runtime.current_user() is not None:
    st.info("You are already signed in.")
    st.stop()

page_header("Create an account", "Choose how you will use FarmTrace. Administrator accounts are created separately.")

role = st.segmented_control(
    "I am registering as",
    options=list(domain.PUBLIC_REGISTRATION_ROLES),
    format_func=lambda r: domain.ROLE_LABELS[r],
    default=domain.ROLE_FARMER,
    required=True,
)
st.caption({
    domain.ROLE_FARMER: "Register farms and produce, request transport and print QR labels.",
    domain.ROLE_DRIVER: "Receive delivery assignments and update trip status. Vehicle details are required.",
    domain.ROLE_CONSUMER: "Verify produce. (Verification also works without an account.)",
}[role])

with st.form("register_form"):
    full_name = st.text_input("Full name", max_chars=150, autocomplete="name")
    email = st.text_input("Email address", max_chars=254, autocomplete="email", placeholder="name@example.com")
    phone = st.text_input(
        "Phone number" + ("" if role == domain.ROLE_CONSUMER else " *"),
        placeholder="08031234567",
        help="Nigerian mobile number. Never shown on public pages.",
    )
    col1, col2 = st.columns(2)
    password = col1.text_input("Password", type="password", autocomplete="new-password",
                               help="At least 8 characters with letters and numbers.")
    confirm = col2.text_input("Confirm password", type="password", autocomplete="new-password")

    driver_details = None
    if role == domain.ROLE_DRIVER:
        st.markdown("**Driver & vehicle details** · kept private, visible only to administrators")
        d1, d2 = st.columns(2)
        licence = d1.text_input("Driver's licence number", max_chars=30)
        plate = d2.text_input("Vehicle registration number", max_chars=20, placeholder="LSD-123-AB")
        vehicle_type = d1.selectbox("Vehicle type", VEHICLE_TYPES)
        capacity = d2.number_input("Load capacity (kg)", min_value=1.0, max_value=100000.0, value=1000.0, step=100.0)
        driver_details = DriverDetails(licence, plate, vehicle_type, capacity)

    agreed = st.checkbox("I confirm these details are accurate and belong to me.")
    submitted = st.form_submit_button("Create account", type="primary", width="stretch")

if submitted:
    if not agreed:
        st.error("Please confirm that your details are accurate.")
    else:
        data = RegistrationData(
            full_name=full_name, email=email, password=password, confirm_password=confirm, role=role,
            phone_number=phone, driver=driver_details,
        )
        created = runtime.act(lambda s: auth.register_user(s, data), rerun=False)
        if created is not None:
            runtime.sign_in(created)
            runtime.flash("Your account is ready.", "success")
            st.rerun()

st.page_link("pages/1_Login.py", label="Already registered? Sign in", icon=":material/login:")
