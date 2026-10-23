"""Table helpers: quick text search over a DataFrame and CSV download buttons."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from farmtrace.services.report_service import to_csv_bytes


def filter_frame(frame: pd.DataFrame, term: str | None) -> pd.DataFrame:
    if frame.empty or not term or not term.strip():
        return frame
    needle = term.strip().lower()
    text_columns = [c for c in frame.columns if frame[c].dtype == object or pd.api.types.is_string_dtype(frame[c])]
    mask = pd.Series(False, index=frame.index)
    for column in text_columns:
        mask |= frame[column].astype(str).str.lower().str.contains(needle, regex=False, na=False)
    return frame[mask]


def searchable_table(frame: pd.DataFrame, *, key: str, placeholder: str = "Search this table…", **kwargs) -> pd.DataFrame:
    term = st.text_input("Search", key=f"{key}_search", placeholder=placeholder, label_visibility="collapsed")
    shown = filter_frame(frame, term)
    st.dataframe(shown, hide_index=True, width="stretch", **kwargs)
    st.caption(f"Showing {len(shown)} of {len(frame)} record(s).")
    return shown


def csv_download(frame: pd.DataFrame, *, filename: str, label: str = "Download CSV", key: str | None = None) -> None:
    st.download_button(
        label, data=to_csv_bytes(frame), file_name=filename, mime="text/csv", icon=":material/download:",
        disabled=frame.empty, key=key or f"dl_{filename}", on_click="ignore",
    )
