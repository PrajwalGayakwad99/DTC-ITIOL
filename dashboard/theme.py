"""Shared design tokens and visual components for DTC-ITOL."""

import html
from datetime import datetime

import streamlit as st


TOKENS = {
    "background": "#080D14",
    "surface": "#0D1520",
    "panel": "#111D2A",
    "border": "#233548",
    "cyan": "#27C7F5",
    "orange": "#FF914D",
    "blue": "#4DAEFF",
    "green": "#19C784",
    "amber": "#FFBF47",
    "red": "#FF4D5A",
    "text": "#EAF2FA",
    "muted": "#91A4B7",
}


CSS = """
<style>
:root {
 --dtc-bg:#080D14; --dtc-surface:#0D1520; --dtc-panel:#111D2A; --dtc-border:#233548;
 --dtc-cyan:#27C7F5; --dtc-orange:#FF914D; --dtc-blue:#4DAEFF; --dtc-green:#19C784;
 --dtc-amber:#FFBF47; --dtc-red:#FF4D5A; --dtc-text:#EAF2FA; --dtc-muted:#91A4B7;
 --dtc-mono:"Cascadia Code", "SFMono-Regular", Consolas, monospace;
}
html, body, [class*="css"] { font-family: Inter, "Segoe UI", system-ui, sans-serif; }
[data-testid="stAppViewContainer"] { background:var(--dtc-bg); color:var(--dtc-text); }
[data-testid="stHeader"] { background:rgba(8,13,20,.92); border-bottom:1px solid rgba(35,53,72,.5); }
[data-testid="stSidebar"] { background:var(--dtc-surface); border-right:1px solid var(--dtc-border); }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color:#B4C3D1; }
[data-testid="stSidebar"] [data-testid="stRadio"] > label { display:none; }
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] { gap:.25rem; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { padding:.43rem .52rem; border:1px solid transparent; border-radius:5px; }
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background:#142333; border-color:var(--dtc-border); }
h1,h2,h3 { color:var(--dtc-text); letter-spacing:-.025em; }
h1 { font-size:1.8rem !important; }
h2 { font-size:1.2rem !important; }
h3 { font-size:1rem !important; }
[data-testid="stMetric"] { background:var(--dtc-panel); border:1px solid var(--dtc-border); border-radius:7px;
 padding:13px 15px; box-shadow:inset 0 1px 0 rgba(255,255,255,.018); transition:border-color .18s ease, transform .18s ease; }
[data-testid="stMetric"]:hover { border-color:#35617b; transform:translateY(-1px); }
[data-testid="stMetricLabel"] { color:var(--dtc-muted); font-size:.75rem; }
[data-testid="stMetricValue"] { color:var(--dtc-text); font-family:var(--dtc-mono); font-size:1.4rem; }
[data-testid="stMetricDelta"] { font-family:var(--dtc-mono); font-size:.72rem; }
[data-testid="stRadio"] label { color:var(--dtc-text); }
[data-testid="stRadio"] input { accent-color:var(--dtc-cyan); }
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) { background:#112433; border-color:#24526A; }
.stTextInput input, .stNumberInput input, .stDateInput input, .stTextArea textarea {
 background:#0D1520 !important; color:var(--dtc-text) !important; border-color:var(--dtc-border) !important; border-radius:5px !important;
}
div[data-baseweb="select"] > div { background:#0D1520; border-color:var(--dtc-border); color:var(--dtc-text); }
div[data-baseweb="select"] span { color:var(--dtc-text); }
div[data-baseweb="popover"] { border:1px solid var(--dtc-border); }
[data-testid="stDataFrame"], [data-testid="stTable"] { border:1px solid var(--dtc-border); border-radius:6px; overflow:hidden; }
div.stButton > button, div.stDownloadButton > button { border-radius:5px; border:1px solid #28516A; background:#102536; color:#DDF7FF; }
div.stButton > button:hover, div.stDownloadButton > button:hover { border-color:var(--dtc-cyan); color:white; }
[data-testid="stAlert"] { border-radius:5px; }
[data-testid="stProgress"] > div > div { background-color:var(--dtc-cyan); transition:width .45s ease; }
[data-testid="stPlotlyChart"] { border:1px solid rgba(35,53,72,.7); border-radius:7px; background:rgba(13,21,32,.55); }
.dtc-brand { display:flex; align-items:center; gap:.7rem; padding:.25rem 0 .9rem; }
.dtc-mark { width:34px; height:34px; display:grid; place-items:center; color:#06111A; background:var(--dtc-cyan);
 font-weight:900; letter-spacing:-.1em; border-radius:5px; box-shadow:0 0 18px rgba(39,199,245,.16); }
.dtc-brand-name { font-weight:800; letter-spacing:.13em; color:var(--dtc-text); font-size:.93rem; }
.dtc-brand-sub { font-size:.62rem; letter-spacing:.09em; color:var(--dtc-muted); margin-top:.12rem; }
.dtc-eyebrow { color:var(--dtc-cyan); font-family:var(--dtc-mono); font-size:.66rem; text-transform:uppercase;
 letter-spacing:.15em; font-weight:700; margin-bottom:.25rem; }
.dtc-subtitle { color:var(--dtc-muted); margin-top:-.4rem; margin-bottom:1rem; font-size:.88rem; }
.dtc-topbar { display:flex; justify-content:space-between; align-items:center; gap:1rem; padding:.6rem .05rem .85rem;
 border-bottom:1px solid var(--dtc-border); margin-bottom:1.1rem; }
.dtc-crumb { color:var(--dtc-muted); font: .68rem var(--dtc-mono); letter-spacing:.04em; text-transform:uppercase; }
.dtc-current { color:var(--dtc-text); font-weight:700; font-size:.94rem; margin-top:.23rem; }
.dtc-top-right { display:flex; align-items:center; justify-content:flex-end; gap:.45rem; flex-wrap:wrap; }
.dtc-badge { display:inline-flex; align-items:center; gap:.4rem; white-space:nowrap; border:1px solid var(--dtc-border);
 padding:.28rem .48rem; color:var(--dtc-muted); background:#0D1722; border-radius:4px; font: .64rem var(--dtc-mono); }
.dtc-dot { width:7px; height:7px; border-radius:50%; background:#788797; display:inline-block; }
.dtc-green .dtc-dot { background:var(--dtc-green); box-shadow:0 0 8px rgba(25,199,132,.42); animation:dtc-pulse 2.1s ease-in-out infinite; }
.dtc-amber .dtc-dot { background:var(--dtc-amber); }
.dtc-red .dtc-dot { background:var(--dtc-red); }
.dtc-blue .dtc-dot { background:var(--dtc-cyan); }
.dtc-readonly { border-color:#28516A; color:#A8E9FA; }
.dtc-pill { display:inline-block; border:1px solid var(--dtc-border); border-radius:4px; padding:.18rem .46rem;
 color:#B4C3D1; background:#0D1722; font:.66rem var(--dtc-mono); letter-spacing:.04em; margin-right:.3rem; }
.dtc-pill-green { border-color:#1D624D; color:#7DE2B9; background:#102920; }
.dtc-pill-amber { border-color:#705727; color:#FFD27A; background:#302610; }
.dtc-pill-red { border-color:#743842; color:#FF9EA7; background:#30171D; }
.dtc-pill-blue { border-color:#28516A; color:#9EEBFC; background:#102536; }
.dtc-divider { height:1px; background:var(--dtc-border); margin:.9rem 0 1rem; }
.dtc-side-label { color:var(--dtc-muted); text-transform:uppercase; letter-spacing:.11em; font:.62rem var(--dtc-mono); margin:.8rem 0 .4rem; }
.dtc-side-meta { color:var(--dtc-text); font:.68rem var(--dtc-mono); overflow-wrap:anywhere; }
.dtc-side-caption { color:var(--dtc-muted); font-size:.66rem; margin-top:.15rem; }
.dtc-panel-title { display:flex; align-items:center; gap:.5rem; color:var(--dtc-muted); font:.68rem var(--dtc-mono);
 letter-spacing:.1em; text-transform:uppercase; margin:0 0 .65rem; }
.dtc-panel-title:before { content:""; width:3px; height:13px; background:var(--dtc-cyan); display:inline-block; }
.dtc-schematic { border:1px solid var(--dtc-border); border-radius:7px; background:var(--dtc-panel); padding:.6rem .7rem .35rem; }
.dtc-schematic-note { color:var(--dtc-muted); font:.65rem var(--dtc-mono); margin:.3rem 0 .15rem; }
.dtc-schematic svg { width:100%; height:auto; }
@keyframes dtc-pulse { 50% { box-shadow:0 0 2px rgba(25,199,132,.18); opacity:.7; } }
@media (max-width: 900px) {
 .dtc-topbar { align-items:flex-start; flex-direction:column; }
 .dtc-top-right { justify-content:flex-start; }
 [data-testid="stMetricValue"] { font-size:1.1rem; }
}
@media (prefers-reduced-motion: reduce) {
 *, *::before, *::after { scroll-behavior:auto !important; animation-duration:.01ms !important; animation-iteration-count:1 !important; transition-duration:.01ms !important; }
}
</style>
"""


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def page_header(eyebrow: str, title: str, subtitle: str) -> None:
    st.markdown(f'<div class="dtc-eyebrow">{html.escape(eyebrow)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<div class="dtc-subtitle">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


def pill(label: str, tone: str = "blue") -> None:
    safe_label = html.escape(str(label))
    safe_tone = tone if tone in {"green", "amber", "red", "blue"} else "blue"
    st.markdown(f'<span class="dtc-pill dtc-pill-{safe_tone}">{safe_label}</span>', unsafe_allow_html=True)


def status_badge(label: str, tone: str = "unknown") -> str:
    safe_label = html.escape(str(label))
    safe_tone = tone if tone in {"green", "amber", "red", "blue"} else "unknown"
    return f'<span class="dtc-badge dtc-{safe_tone}"><span class="dtc-dot"></span>{safe_label}</span>'


def top_navigation(page_title: str, backend_status: str, backend_tone: str,
                   device_status: str, device_tone: str, refresh_seconds: int = 5) -> None:
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    st.markdown(
        '<div class="dtc-topbar">'
        f'<div><div class="dtc-crumb">DTC-ITOL / THERMAL OPERATIONS / {html.escape(page_title.upper())}</div></div>'
        '<div class="dtc-top-right">'
        f'{status_badge(f"API {backend_status.upper()}", backend_tone)}'
        f'{status_badge(f"DEVICE {device_status.upper()}", device_tone)}'
        f'<span class="dtc-badge">REFRESH {refresh_seconds}s</span>'
        f'<span class="dtc-badge">{html.escape(timestamp)}</span>'
        '<span class="dtc-badge dtc-readonly">READ ONLY</span>'
        '</div></div>',
        unsafe_allow_html=True,
    )


def sidebar_brand() -> None:
    st.markdown(
        '<div class="dtc-brand"><div class="dtc-mark">D/</div><div>'
        '<div class="dtc-brand-name">DTC-ITOL</div>'
        '<div class="dtc-brand-sub">THERMAL OPERATIONS SYSTEM</div></div></div>',
        unsafe_allow_html=True,
    )


def provenance_caption(provenance: str, valid: bool = True) -> str:
    labels = {"measured": "MEASURED", "simulated": "SIMULATED", "estimated": "ESTIMATED", "unknown": "SOURCE UNKNOWN"}
    state = labels.get(str(provenance).lower(), "SOURCE UNKNOWN")
    return f"{state} · {'VALID' if valid else 'INVALID'}"
