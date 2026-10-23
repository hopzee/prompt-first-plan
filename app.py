"""FarmTrace — Streamlit entry point.

Run locally with:

    streamlit run app.py

This script (1) loads configuration, (2) verifies the PostgreSQL database is
reachable and migrated, (3) restores the signed-in user from the database,
(4) builds role-specific navigation, and (5) routes QR scans
(``/?verify=<token>``) straight to the public verification page.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="FarmTrace", page_icon="🌱", layout="wide", initial_sidebar_state="auto")

from farmtrace import domain  # noqa: E402  (set_page_config must run first)
from farmtrace.components import home, runtime, sidebar, theme  # noqa: E402

theme.apply()
settings = runtime.boot()
user = runtime.resolve_current_user()

# A scanned QR code opens "/?verify=<token>": make verification the landing page.
verify_requested = bool(st.query_params.get("verify"))

PAGES = {
    "home": st.Page(home.render, title="Home", icon=":material/home:", url_path="home", default=not verify_requested),
    "verify": st.Page("pages/10_Verify_Produce.py", title="Verify produce", icon=":material/qr_code_scanner:",
                      url_path="verify", default=verify_requested),
    "login": st.Page("pages/1_Login.py", title="My account" if user else "Sign in",
                     icon=":material/person:" if user else ":material/login:", url_path="account"),
    "register": st.Page("pages/2_Register.py", title="Create account", icon=":material/person_add:",
                        url_path="register"),
    "farmer": st.Page("pages/3_Farmer_Dashboard.py", title="Farmer dashboard", icon=":material/agriculture:",
                      url_path="farmer"),
    "driver": st.Page("pages/4_Driver_Dashboard.py", title="Driver dashboard", icon=":material/local_shipping:",
                      url_path="driver"),
    "admin": st.Page("pages/5_Admin_Dashboard.py", title="Admin dashboard", icon=":material/admin_panel_settings:",
                     url_path="admin"),
    "produce": st.Page("pages/6_Produce_Management.py", title="Produce batches", icon=":material/inventory_2:",
                       url_path="produce"),
    "transport": st.Page("pages/7_Transport_Requests.py", title="Transport requests", icon=":material/route:",
                         url_path="transport"),
    "tracking": st.Page("pages/8_Delivery_Tracking.py", title="Delivery tracking", icon=":material/timeline:",
                        url_path="tracking"),
    "reports": st.Page("pages/9_Reports.py", title="Reports", icon=":material/summarize:", url_path="reports"),
}


def workspace_pages() -> list:
    if user is None:
        return []
    keys: list[str] = []
    if user.has_role(domain.ROLE_ADMIN):
        keys += ["admin", "produce", "transport", "tracking", "reports"]
    if user.has_role(domain.ROLE_FARMER):
        keys += ["farmer", "produce", "transport", "tracking", "reports"]
    if user.has_role(domain.ROLE_DRIVER):
        keys += ["driver", "tracking", "reports"]
    return [PAGES[k] for k in dict.fromkeys(keys)]  # de-duplicate, keep order


def dashboard_page():
    if user is None:
        return PAGES["home"]
    return {
        domain.ROLE_ADMIN: PAGES["admin"],
        domain.ROLE_FARMER: PAGES["farmer"],
        domain.ROLE_DRIVER: PAGES["driver"],
    }.get(user.primary_role, PAGES["verify"])


navigation = {"FarmTrace": [PAGES["home"], PAGES["verify"]]}
if user is None:
    navigation["FarmTrace"] += [PAGES["login"], PAGES["register"]]
else:
    work = workspace_pages()
    if work:
        navigation["My work"] = work
    navigation["Account"] = [PAGES["login"]]

current_page = st.navigation(navigation)
sidebar.render(user, settings)

if st.session_state.pop(runtime.AFTER_LOGIN_KEY, False):
    st.switch_page(dashboard_page())
if st.session_state.pop(runtime.AFTER_LOGOUT_KEY, False):
    st.switch_page(PAGES["home"])

runtime.show_flash()
current_page.run()
