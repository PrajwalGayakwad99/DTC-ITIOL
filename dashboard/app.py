"""Read-only live DTC-ITOL monitor backed by ESP32 telemetry in FastAPI/SQLite."""

from __future__ import annotations

import os
from html import escape
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.analytics import RANGE_DELTAS, filter_time_range
from dashboard.data_access import freshness, load_snapshot, measured_monitoring_records
from dashboard.presentation import sensor_display_name, sensor_provenance_label, temperature_history_frame
from dashboard.theme import apply_theme

REFRESH_SECONDS = 3
EXPECTED_CHANNELS = (
    ("thermocouple_primary", "Primary heater · MAX6675 channel 1"),
    ("thermocouple_secondary", "Secondary heater 1 control · MAX6675 channel 2"),
    ("ds18b20_secondary_heater", "Secondary heater 1 monitoring · DS18B20 · expected ROM …00AB"),
    ("ds18b20_secondary_heater_2", "Secondary heater 2 monitoring · DS18B20 · expected ROM …00F2"),
    ("ds18b20_coolant", "Coolant temperature · DS18B20 · expected ROM …00B3"),
)


def _hardware_records(records: list[dict]) -> list[dict]:
    """Exclude simulation packets and non-measured samples from operational views."""
    return measured_monitoring_records(records)


def _latest_temperature_sensors(latest: dict | None) -> dict[str, dict]:
    if not latest:
        return {}
    return {
        sensor.get("name"): sensor
        for sensor in latest.get("sensors", [])
        if sensor.get("kind", "temperature") == "temperature"
        and str(sensor.get("provenance", "")).lower() == "measured"
    }


def _reading_state(sensor: dict | None, packet_age: float | None) -> str:
    if sensor is None:
        return "INVALID"
    if sensor.get("error") == "location_mapping_unconfigured":
        return "NOT CONFIGURED"
    if not sensor.get("valid") or sensor.get("value") is None:
        return "INVALID"
    if packet_age is not None and packet_age > 15:
        return "STALE"
    return "VALID"


def _temperature_card(label: str, sensor: dict | None, packet_age: float | None) -> None:
    state = _reading_state(sensor, packet_age)
    if sensor is None or not sensor.get("valid") or sensor.get("value") is None:
        value = "N/A"
    else:
        value = f"{float(sensor['value']):.2f} {escape(str(sensor.get('unit') or '°C'))}"
    st.markdown(
        f'<div class="sensor-card"><div class="sensor-title">{escape(label)}</div>'
        f'<div class="sensor-value">{value}</div>'
        f'<div class="sensor-status sensor-{state.lower().replace(" ", "-")}">{state}</div></div>',
        unsafe_allow_html=True,
    )
    if sensor:
        st.caption(sensor_provenance_label(sensor))
        if sensor.get("error"):
            st.caption(f"Diagnostic: {sensor['error']}")


def _thermal_history(records: list[dict], range_name: str) -> None:
    frame = temperature_history_frame(_hardware_records(records))
    frame = filter_time_range(frame, range_name)
    if frame.empty:
        st.info("No valid measured history is available for this time range.")
        return
    figure = px.line(
        frame.sort_values("time"), x="time", y="temperature", color="sensor",
        custom_data=["provenance", "sensor_rom"],
        labels={"time": "Time", "temperature": "Temperature (°C)", "sensor": "Physical channel"},
        color_discrete_sequence=["#27C7F5", "#4DAEFF", "#19C784", "#FFBF47", "#B99BFF"],
    )
    figure.update_traces(
        connectgaps=False,
        hovertemplate="%{x|%Y-%m-%d %H:%M:%S}<br>%{y:.2f} °C<br>Source: %{customdata[0]}<br>ROM: %{customdata[1]}<extra>%{fullData.name}</extra>",
    )
    figure.update_layout(
        template="plotly_dark", height=390, autosize=True, hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(10,24,37,.8)",
        margin=dict(l=8, r=8, t=20, b=8),
        xaxis=dict(title="Time", gridcolor="#1d3448"),
        yaxis=dict(title="Temperature (°C)", gridcolor="#1d3448", rangemode="tozero"),
        legend_title_text="Physical channel",
    )
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False, "responsive": True})


def _heater_status(latest: dict | None) -> None:
    st.subheader("Heater status · read only")
    heater_records = [
        sensor for sensor in (latest or {}).get("sensors", [])
        if sensor.get("kind") == "heater_state"
    ]
    if not heater_records:
        st.info("No heater status records are available in the latest ESP32 packet.")
        return
    st.caption("Firmware-reported commands only. No actuator feedback circuit is present to verify physical switching.")
    cols = st.columns(3)
    for col, heater in zip(cols, heater_records):
        with col:
            col.metric(heater.get("name", "Heater"), heater.get("state") or "UNKNOWN",
                       delta=f"Intended {heater.get('intended_state') or 'UNKNOWN'}")
            col.caption(f"Mode: {heater.get('control_mode') or 'not reported'} · sensor {'valid' if heater.get('valid') else 'invalid'}")
            if heater.get("error"):
                col.warning(f"Fault / inhibit: {heater['error']}")
    st.caption("Peltier: unassigned and disabled. Pump and fan status: not reported by this firmware.")


def _render_live(api_base: str, device_filter: str, range_name: str) -> None:
    snapshot = load_snapshot(api_base, device_filter, 1000)
    records = snapshot["records"]
    latest = snapshot.get("latest_hardware")
    device_info = snapshot.get("device_status") or {}
    health = snapshot.get("health") or {}
    backend_online = not snapshot.get("health_error") and health.get("status") == "ok"
    fallback_state = freshness(latest)[0]
    fallback_device_state = {"NO TELEMETRY": "OFFLINE", "NO HARDWARE DATA": "OFFLINE"}.get(fallback_state.upper(), fallback_state.upper())
    device_state = str(device_info.get("state") or fallback_device_state).upper()
    latest_packet = snapshot.get("latest_packet") or {}
    source_state = "MEASURED DEVICE TELEMETRY" if latest else (
        "SIMULATION" if latest_packet.get("mode") == "simulation" else "DEVICE OFFLINE"
    )
    latest_stamp = latest.get("timestamp_dt") if latest else None
    latest_text = latest_stamp.astimezone().strftime("%d %b %Y · %H:%M:%S %Z") if latest_stamp else "No measured packet"
    if device_info.get("telemetry_age_seconds") is not None:
        packet_age = float(device_info["telemetry_age_seconds"])
    elif latest_stamp:
        packet_age = max(0.0, (datetime.now(timezone.utc) - latest_stamp).total_seconds())
    else:
        packet_age = None

    st.markdown(
        f'<div class="hero"><div><div class="eyebrow">DTC-ITOL · THERMAL MONITORING</div>'
        f'<div class="hero-title">Thermal Monitoring System</div>'
        f'<div class="hero-subtitle">Last device packet · {latest_text}</div></div>'
        f'<div class="status-chips"><span class="chip">{source_state}</span>'
        f'<span class="chip {"good" if backend_online else "bad"}">BACKEND {"ONLINE" if backend_online else "OFFLINE"}</span>'
        f'<span class="chip {"good" if device_state == "CONNECTED" else "bad"}">ESP32 {device_state}</span>'
        f'<span class="chip">REFRESH {REFRESH_SECONDS}s</span></div></div>',
        unsafe_allow_html=True,
    )

    if snapshot.get("telemetry_error"):
        st.error(f"Telemetry API unavailable: {snapshot['telemetry_error']}")
    if snapshot.get("health_error"):
        st.warning(f"FastAPI health check unavailable: {snapshot['health_error']}")
    elif not backend_online:
        st.warning("FastAPI did not report healthy status.")
    if not latest:
        st.info("Waiting for measured ESP32 telemetry. No temperatures are fabricated or filled with zero values.")

    metrics = st.columns(4)
    metrics[0].metric("Device ID", latest.get("device_id", "—") if latest else "—")
    metrics[1].metric("Controller mode", device_info.get("controller_mode") or "Not reported")
    metrics[2].metric("Wi-Fi signal", f"{device_info['wifi_rssi_dbm']} dBm" if device_info.get("wifi_rssi_dbm") is not None else "Not reported")
    metrics[3].metric("ESP32 IP", device_info.get("ip_address") or "Not reported")

    latest_sensors = _latest_temperature_sensors(latest)
    matched: dict[str, dict | None] = {}
    for key, _label in EXPECTED_CHANNELS:
        reading = latest_sensors.get(key)
        if reading:
            label = sensor_display_name(reading).lower()
            if "declared role mismatch" in label or "unverified" in label:
                reading = None
        matched[key] = reading

    st.subheader("Live temperature channels")
    for row_start in range(0, len(EXPECTED_CHANNELS), 2):
        cols = st.columns(2, gap="medium")
        for col, (key, default_label) in zip(cols, EXPECTED_CHANNELS[row_start:row_start + 2]):
            reading = matched[key]
            label = sensor_display_name(reading) if reading else default_label
            with col:
                _temperature_card(label, reading, packet_age)

    if latest:
        status_counts = {"VALID": 0, "INVALID": 0, "STALE": 0}
        for key, _label in EXPECTED_CHANNELS:
            status_counts[_reading_state(matched.get(key), packet_age)] += 1
        st.caption("Configured channels · " + " · ".join(f"{status} {count}" for status, count in status_counts.items()))
    _heater_status(latest)

    st.subheader("Temperature history")
    _thermal_history(records, range_name)


st.set_page_config(page_title="DTC-ITOL · Thermal Monitoring", page_icon="🌡️", layout="wide", initial_sidebar_state="collapsed")
apply_theme()
st.markdown("""
<style>
section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {display:none}
.block-container {max-width:1450px;padding-top:1.2rem;padding-bottom:2rem}
.hero {display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:1rem 1.15rem;
  margin:.2rem 0 1rem;border:1px solid #20394e;border-radius:14px;background:linear-gradient(115deg,#0e1b2a,#0a1522)}
.eyebrow {color:#42d7f1;font-size:.68rem;font-weight:850;letter-spacing:.17em}
.hero-title {font-size:1.35rem;font-weight:750;color:#eef5fc;margin-top:.2rem}
.hero-subtitle {color:#91a6ba;font-size:.77rem;margin-top:.2rem}
.status-chips {display:flex;gap:.4rem;flex-wrap:wrap;justify-content:flex-end}
.chip {padding:.38rem .55rem;border-radius:999px;border:1px solid #2b455a;background:#0b1b29;color:#c5d3df;font-size:.66rem;font-weight:700}
.chip.good {color:#8aebbd;border-color:#24684f;background:#0c261f}.chip.bad {color:#ffabb0;border-color:#743842;background:#30171d}
.sensor-card {min-height:132px;padding:.85rem 1rem;border:1px solid #20394e;border-radius:12px;background:linear-gradient(145deg,#102133,#0b1724);margin-bottom:.25rem}
.sensor-title {color:#e8f1f9;font-size:.82rem;font-weight:750;min-height:2.3em}
.sensor-value {font-size:1.65rem;font-weight:780;color:#f3f8fc;margin-top:.25rem}
.sensor-status {font-size:.66rem;font-weight:800;letter-spacing:.08em;margin-top:.3rem;color:#8aebbd}
.sensor-invalid,.sensor-stale {color:#ffb760}.sensor-not-configured {color:#9db1c3}
[data-testid="stPlotlyChart"] {width:100%;border:1px solid #20394e;border-radius:12px;overflow:hidden}
@media(max-width:700px){.hero{align-items:flex-start;flex-direction:column}.status-chips{justify-content:flex-start}.block-container{padding-left:.75rem;padding-right:.75rem}}
</style>
""", unsafe_allow_html=True)

api_base = os.environ.get("DTC_ITOL_API_BASE", "http://127.0.0.1:8000").rstrip("/")
default_device_id = os.environ.get("DTC_ITOL_DEVICE_ID", "")
toolbar = st.columns([2, 1, 1])
with toolbar[0]:
    api_base = st.text_input("FastAPI address", value=api_base, help="Example: http://192.168.1.20:8000")
with toolbar[1]:
    device_filter = st.text_input("Device ID filter", value=default_device_id)
with toolbar[2]:
    range_name = st.selectbox("History range", list(RANGE_DELTAS), index=2)


@st.fragment(run_every=f"{REFRESH_SECONDS}s")
def live_view() -> None:
    _render_live(api_base, device_filter.strip(), range_name)


live_view()
