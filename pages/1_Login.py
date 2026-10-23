"""Sign in (signed-out visitors) or My account (signed-in users)."""

import streamlit as st

from farmtrace.authentication import auth
from farmtrace.components import runtime
from farmtrace.components.dashboard_cards import key_values, page_header

user = runtime.current_user()

if user is None:
    page_header("Sign in", "Use the email address and password you registered with.")
    left, _ = st.columns([3, 2])
    with left:
        with st.form("login_form"):
            email = st.text_input("Email address", autocomplete="email", placeholder="name@example.com")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            signed_in = runtime.query(lambda s: auth.authenticate(s, email, password))
            if signed_in is not None:
                runtime.sign_in(signed_in)
                st.rerun()
        st.page_link("pages/2_Register.py", label="New here? Create an account", icon=":material/person_add:")
        st.caption(
            "Forgot your password? Ask a FarmTrace administrator to issue a temporary one. "
            "Email-based password reset is not configured in this deployment."
        )
else:
    page_header("My account", "Your profile and sign-in details.")
    with st.container(border=True):
        key_values([
            ("Name", user.full_name),
            ("Email", user.email),
            ("Role(s)", user.role_label),
        ])
    st.subheader("Change password")
    with st.form("change_password", clear_on_submit=True):
        current = st.text_input("Current password", type="password", autocomplete="current-password")
        new = st.text_input("New password", type="password", autocomplete="new-password",
                            help="At least 8 characters, with letters and numbers.")
        confirm = st.text_input("Confirm new password", type="password", autocomplete="new-password")
        if st.form_submit_button("Update password", type="primary"):
            runtime.act(lambda s: auth.change_password(s, user, current, new, confirm),
                        success="Password updated.")
    if st.button("Sign out", icon=":material/logout:"):
        runtime.sign_out()
        st.rerun()
