"""Page-level access guards (a convenience layer — services enforce the real rules)."""

from __future__ import annotations

import streamlit as st

from farmtrace import domain
from farmtrace.authentication.permissions import CurrentUser
from farmtrace.components import runtime


def require_access(*roles: str) -> CurrentUser:
    user = runtime.current_user()
    if user is None:
        st.info("Please sign in to view this page.", icon=":material/lock:")
        st.page_link("pages/1_Login.py", label="Go to sign in", icon=":material/login:")
        st.stop()
    if roles and not user.has_role(*roles):
        allowed = ", ".join(domain.ROLE_LABELS[r] for r in roles)
        st.error(f"This page is only available to: {allowed}.", icon=":material/block:")
        st.stop()
    return user
