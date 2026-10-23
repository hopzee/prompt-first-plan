"""Visual identity: agricultural green, warm neutral background, restrained accents.

Base colours and fonts are set in ``.streamlit/config.toml``; this module adds a
small amount of CSS for badges, cards and the tracking timeline.
"""

from __future__ import annotations

import streamlit as st

_CSS = """
<style>
:root {
  --ft-green-900: #1B4D20;
  --ft-green-700: #2E7D32;
  --ft-green-100: #E6F0DF;
  --ft-soil: #7A5230;
  --ft-sun: #E8A33D;
  --ft-ink: #1E2A1E;
  --ft-muted: #5B6656;
  --ft-line: #D9E1CF;
  --ft-surface: #FFFFFF;
}
.block-container { padding-top: 2.2rem; max-width: 1180px; }
h1, h2, h3 { letter-spacing: -0.01em; }

.ft-hero {
  background: linear-gradient(135deg, var(--ft-green-900) 0%, var(--ft-green-700) 70%, #4F8F3A 100%);
  color: #FFFFFF; border-radius: 18px; padding: 2.2rem 2rem; margin-bottom: 1.4rem;
}
.ft-hero h1 { color: #FFFFFF; font-size: 2.3rem; margin: 0 0 .4rem 0; line-height: 1.15; }
.ft-hero p { color: #E9F3E3; font-size: 1.05rem; max-width: 46rem; margin: 0; }
.ft-eyebrow { text-transform: uppercase; letter-spacing: .12em; font-size: .75rem; color: #CFE6C4; font-weight: 700; }

.ft-page-sub { color: var(--ft-muted); margin-top: -.6rem; margin-bottom: 1.2rem; }

.ft-badge { display: inline-block; padding: .18rem .6rem; border-radius: 999px; font-size: .8rem;
  font-weight: 600; border: 1px solid transparent; white-space: nowrap; margin-right: .3rem; }
.ft-status-Pending    { background: #EEF0EA; color: #3F473B; border-color: #D5DACF; }
.ft-status-Approved   { background: #E3EEF9; color: #1D4E89; border-color: #C3D8F0; }
.ft-status-Assigned   { background: #E0F2F1; color: #00594F; border-color: #B9E0DC; }
.ft-status-Dispatched { background: #FFF1DC; color: #7A4300; border-color: #F5D7A8; }
.ft-status-In-Transit { background: #FFF6D6; color: #6B4E00; border-color: #F0E0A0; }
.ft-status-Delivered  { background: #E3F2E1; color: #1B5E20; border-color: #BFDDBB; }
.ft-status-Cancelled  { background: #FBE9E7; color: #9A2A1E; border-color: #F2C4BD; }
.ft-status-Delayed    { background: #9A2A1E; color: #FFFFFF; }
.ft-status-Registered { background: #EEF0EA; color: #3F473B; border-color: #D5DACF; }
.ft-status-Awaiting   { background: #E3EEF9; color: #1D4E89; border-color: #C3D8F0; }
.ft-status-InTransport{ background: #FFF1DC; color: #7A4300; border-color: #F5D7A8; }
.ft-demo { background: #FFF4CC; color: #6B5200; border: 1px dashed #C9A227; }

.ft-empty { border: 1px dashed var(--ft-line); border-radius: 14px; padding: 1.4rem; text-align: center;
  color: var(--ft-muted); background: rgba(255,255,255,.6); }
.ft-empty strong { color: var(--ft-ink); display: block; margin-bottom: .25rem; }

.ft-steps { display: flex; flex-wrap: wrap; gap: .35rem; margin: .4rem 0 1rem; }
.ft-step { padding: .3rem .7rem; border-radius: 999px; font-size: .8rem; background: #EEF0EA; color: #6A7365; }
.ft-step.done { background: var(--ft-green-100); color: var(--ft-green-900); }
.ft-step.current { background: var(--ft-green-700); color: #FFFFFF; font-weight: 700; }
.ft-step.cancelled { background: #FBE9E7; color: #9A2A1E; font-weight: 700; }

.ft-timeline { border-left: 3px solid var(--ft-line); margin: .4rem 0 0 .45rem; padding-left: 1.1rem; }
.ft-event { position: relative; margin-bottom: 1rem; }
.ft-event::before { content: ""; position: absolute; left: -1.55rem; top: .3rem; width: .75rem; height: .75rem;
  border-radius: 50%; background: var(--ft-green-700); border: 2px solid #FFFFFF; box-shadow: 0 0 0 1px var(--ft-line); }
.ft-event.warn::before { background: #9A2A1E; }
.ft-event-title { font-weight: 700; color: var(--ft-ink); }
.ft-event-meta { font-size: .82rem; color: var(--ft-muted); }
.ft-event-body { font-size: .92rem; margin-top: .15rem; }

.ft-kv { display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1rem; font-size: .95rem; }
.ft-kv dt { color: var(--ft-muted); }
.ft-kv dd { margin: 0; color: var(--ft-ink); font-weight: 500; }

@media (max-width: 640px) {
  .ft-hero { padding: 1.5rem 1.2rem; }
  .ft-hero h1 { font-size: 1.7rem; }
  .ft-kv { grid-template-columns: 1fr; }
  .ft-kv dd { margin-bottom: .4rem; }
}
</style>
"""


def apply() -> None:
    st.html(_CSS)
