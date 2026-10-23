"""Reusable presentation blocks. All user-supplied text is HTML-escaped."""

from __future__ import annotations

import html
from typing import Iterable, Sequence

import streamlit as st

from farmtrace import domain
from farmtrace.utils.helpers import format_datetime

_BATCH_CLASS = {
    domain.BATCH_REGISTERED: "Registered",
    domain.BATCH_AWAITING_TRANSPORT: "Awaiting",
    domain.BATCH_IN_TRANSPORT: "InTransport",
    domain.BATCH_DELIVERED: "Delivered",
    domain.BATCH_CANCELLED: "Cancelled",
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def page_header(title: str, subtitle: str | None = None) -> None:
    st.title(title)
    if subtitle:
        st.markdown(f"<p class='ft-page-sub'>{esc(subtitle)}</p>", unsafe_allow_html=True)


def status_badge(status: str | None, delayed: bool = False) -> str:
    if not status:
        return "<span class='ft-badge ft-status-Pending'>Not requested</span>"
    badge = f"<span class='ft-badge ft-status-{esc(status)}'>{esc(status)}</span>"
    if delayed:
        badge += f"<span class='ft-badge ft-status-Delayed'>{domain.DELAYED_LABEL}</span>"
    return badge


def batch_badge(status: str) -> str:
    return f"<span class='ft-badge ft-status-{_BATCH_CLASS.get(status, 'Registered')}'>{esc(status)}</span>"


def demo_badge(is_demo: bool) -> str:
    return "<span class='ft-badge ft-demo'>DEMO RECORD</span>" if is_demo else ""


def show_html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def metric_cards(items: Sequence[tuple[str, object, str | None]], per_row: int = 4) -> None:
    for start in range(0, len(items), per_row):
        columns = st.columns(per_row)
        for column, (label, value, help_text) in zip(columns, items[start:start + per_row]):
            with column.container(border=True):
                st.metric(label, value, help=help_text)


def empty_state(title: str, message: str) -> None:
    show_html(f"<div class='ft-empty'><strong>{esc(title)}</strong>{esc(message)}</div>")


def key_values(pairs: Iterable[tuple[str, object]]) -> None:
    rows = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v) if v not in (None, '') else '—'}</dd>" for k, v in pairs)
    show_html(f"<dl class='ft-kv'>{rows}</dl>")


def progress_steps(status: str) -> None:
    flow = (domain.STATUS_PENDING, domain.STATUS_APPROVED, domain.STATUS_ASSIGNED, domain.STATUS_DISPATCHED,
            domain.STATUS_IN_TRANSIT, domain.STATUS_DELIVERED)
    if status == domain.STATUS_CANCELLED:
        show_html("<div class='ft-steps'><span class='ft-step cancelled'>Cancelled</span></div>")
        return
    current = flow.index(status) if status in flow else -1
    parts = []
    for index, step in enumerate(flow):
        css = "done" if index < current else "current" if index == current else ""
        parts.append(f"<span class='ft-step {css}'>{esc(step)}</span>")
    show_html("<div class='ft-steps'>" + "".join(parts) + "</div>")


def timeline(events: Sequence[dict], tz_name: str) -> None:
    """``events``: dicts with title, timestamp, and optional status, actor, location, body, warn."""
    if not events:
        empty_state("No events yet", "Tracking events will appear here as the delivery progresses.")
        return
    blocks = []
    for event in events:
        meta = [format_datetime(event["timestamp"], tz_name)]
        if event.get("status"):
            meta.append(f"Status: {event['status']}")
        if event.get("actor"):
            meta.append(f"By {event['actor']}")
        location = ""
        if event.get("location"):
            location = (f"<div class='ft-event-meta'>📍 {esc(event['location'])} · "
                        f"<em>{esc(domain.LOCATION_SOURCE_LABEL)}</em></div>")
        body = f"<div class='ft-event-body'>{esc(event['body'])}</div>" if event.get("body") else ""
        css = "ft-event warn" if event.get("warn") else "ft-event"
        blocks.append(
            f"<div class='{css}'><div class='ft-event-title'>{esc(event['title'])}</div>"
            f"<div class='ft-event-meta'>{esc(' · '.join(meta))}</div>{location}{body}</div>"
        )
    show_html("<div class='ft-timeline'>" + "".join(blocks) + "</div>")
