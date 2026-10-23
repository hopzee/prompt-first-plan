"""Sidebar: brand, signed-in user and sign-out."""

from __future__ import annotations

import streamlit as st

from farmtrace import APP_NAME
from farmtrace.authentication.permissions import CurrentUser
from farmtrace.components import runtime
from farmtrace.config.settings import Settings


def render(user: CurrentUser | None, settings: Settings) -> None:
    with st.sidebar:
        st.markdown(f"### 🌱 {APP_NAME}")
        st.caption("Farm produce transport & traceability")
        if settings.app_env != "production":
            st.caption(f":orange-badge[{settings.app_env.upper()} ENVIRONMENT]")
        st.divider()
        if user is None:
            st.caption("Not signed in. Anyone can verify produce without an account.")
            return
        st.markdown(f"**{user.full_name}**  \n{user.email}")
        st.caption(user.role_label)
        if st.button("Sign out", icon=":material/logout:", width="stretch", key="sidebar_sign_out"):
            runtime.sign_out()
            st.rerun()
