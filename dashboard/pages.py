"""Seven read-only engineering views backed by the current telemetry API."""

from datetime import datetime, timedelta, timezone
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.analytics import (
    RANGE_DELTAS,
    experiment_telemetry,
    filter_time_range,
    summarize_sensor,
    trend_slope_c_per_hour,
)
from dashboard.data_access import freshness
from dashboard.experiment_store import display_experiment, list_experiments, log_experiment_details
from dashboard.power_model import estimate_power
from dashboard.presentation import (
    VERIFIED_DS18B20_ROM, configured_sensor_warnings, history_quality_counts,
    sensor_display_name, sensor_identity, sensor_rom, sensor_status_counts, sensor_status_rows, temperature_history_frame,
)
from dashboard.theme import page_header, pill, provenance_caption


PALETTE = ["#27C7F5", "#4DAEFF", "#19C784", "#FFBF47", "#B99BFF", "#FF914D"]
SENSOR_TARGETS = {"primary": 150.0, "secondary": 100.0}
def _all_sensor_names(records: list[dict]) -> list[str]:
    names = {sensor_display_name(sensor) for record in records for sensor in record.get("sensors", [])
             if sensor.get("kind", "temperature") == "temperature"}
    return sorted(names)


def _mapping_controls(records: list[dict], key_prefix: str = "thermal") -> tuple[str, str]:
    names = _all_sensor_names(records)
    options = ["Unassigned", *names]
    first, second = st.columns(2)
    primary_key, secondary_key = f"{key_prefix}_primary_channel", f"{key_prefix}_secondary_channel"
    with first:
        primary = st.selectbox("Primary heater channel", options, key=primary_key)
    with second:
        secondary = st.selectbox("Secondary heater channel", options, key=secondary_key)
    st.caption("Channel mapping only changes dashboard labels and calculations; it sends no commands to hardware.")
    return primary, secondary


def _sensor_by_name(record: dict | None, name: str) -> dict | None:
    if not record or name == "Unassigned":
        return None
    return next((sensor for sensor in record.get("sensors", [])
                 if sensor_display_name(sensor) == name and sensor.get("kind", "temperature") == "temperature"), None)


def _heater_records(record: dict | None) -> list[dict]:
    if not record:
        return []
    return [sensor for sensor in record.get("sensors", []) if sensor.get("kind") == "heater_state"]


def _heater_status_panel(record: dict | None) -> None:
    heaters = _heater_records(record)
    st.subheader("Independent heater status · read only")
    if not heaters:
        st.info("No heater-control status is present in this telemetry packet. Temperature sensor data remains available separately.")
        return
    st.caption(f"Operating mode: {heaters[0].get('control_mode') or 'unknown'} · dashboard issues no hardware commands.")
    cols = st.columns(3)
    for col, heater in zip(cols, heaters):
        with col:
            value = _display_sensor_value(heater)
            target = heater.get("target_c")
            st.metric(heater.get("name", "Heater"), value,
                      delta=f"Target {float(target):.1f}°C" if target is not None else None)
            st.caption(f"State: {heater.get('state') or 'UNKNOWN'} · Sensor: {'valid' if heater.get('valid') else 'invalid'} · {str(heater.get('provenance') or 'unknown').upper()}")
            heater_name = heater.get("name")
            if heater_name == "primary_mk8":
                st.caption("Sensor source: MAX6675 primary thermocouple · channel 1")
            elif heater_name == "secondary_heater_1":
                st.caption("Sensor source: existing MAX6675 secondary thermocouple · channel 2")
            elif heater_name == "secondary_heater_2":
                actual_rom = str(heater.get("sensor_rom") or "").upper()
                expected_rom = VERIFIED_DS18B20_ROM["ds18b20_secondary_heater_2"]
                st.caption("Sensor source: DS18B20 · ROM …00F2" if actual_rom == expected_rom
                           else "Sensor source: DS18B20 mapping unverified")
            progress = heater.get("progress_pct")
            if progress is not None:
                st.progress(min(1.0, max(0.0, float(progress) / 100.0)), text=f"Calculated temperature progress · {float(progress):.1f}%")
            if heater.get("error"):
                if (str(heater.get("state", "")).upper() == "FAULT" and heater.get("valid")
                        and heater_name == "secondary_heater_2"
                        and str(heater.get("sensor_rom") or "").upper() == VERIFIED_DS18B20_ROM["ds18b20_secondary_heater_2"]):
                    st.warning(
                        f"Latched fault: {heater['error']}. The current reading from the configured F2 sensor is valid; "
                        "firmware keeps the fault latched until an explicit local reset passes its valid, fresh, "
                        "below-threshold checks for all heater sensors."
                    )
                else:
                    st.warning(f"Fault / inhibit: {heater['error']}")


def _display_sensor_value(sensor: dict | None) -> str:
    if not sensor:
        return "Not mapped"
    if sensor_identity(sensor) == "not_configured:primary_ds18b20":
        return "Not configured"
    if not sensor.get("valid") or sensor.get("value") is None:
        return "Invalid"
    return f"{float(sensor['value']):.2f} {sensor.get('unit', '°C')}"


def _status_temperature(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{numeric:.2f}°C" if math.isfinite(numeric) else "—"


def _sensor_cards(sensors: list[dict], empty_message: str = "No sensors in the latest telemetry packet.") -> None:
    if not sensors:
        st.info(empty_message)
        return
    columns = st.columns(min(5, len(sensors)))
    for index, sensor in enumerate(sensors):
        with columns[index % len(columns)]:
            st.metric(sensor_display_name(sensor), _display_sensor_value(sensor))
            st.caption(provenance_caption(sensor.get("provenance", "unknown"), bool(sensor.get("valid"))))
            if sensor.get("error"):
                st.caption(f"Sensor status: {sensor['error']}")


def _thermal_system_diagram() -> None:
    st.markdown('<div class="dtc-panel-title">Thermal system concept · schematic, not live state</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="dtc-schematic" role="img" aria-label="Conceptual thermal loop from primary heater and evaporator through secondary condenser and pump to radiator heat rejection">
      <svg viewBox="0 0 1080 245" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <marker id="dtc-arrow" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto">
            <path d="M0 0L9 4.5L0 9Z" fill="#27C7F5"/>
          </marker>
        </defs>
        <path d="M224 84H294M478 84H548M732 84H802M896 122V178H150V122" fill="none" stroke="#27C7F5" stroke-width="3" stroke-dasharray="7 7" marker-end="url(#dtc-arrow)" opacity=".82"/>
        <rect x="28" y="40" width="196" height="88" rx="5" fill="#201B1B" stroke="#FF914D" stroke-width="1.5"/>
        <text x="126" y="70" fill="#FFB080" text-anchor="middle" font-size="13" font-family="Segoe UI, sans-serif">PRIMARY THERMAL STAGE</text>
        <text x="126" y="96" fill="#EAF2FA" text-anchor="middle" font-size="15" font-weight="700" font-family="Segoe UI, sans-serif">Heater · evaporator</text>
        <text x="126" y="115" fill="#91A4B7" text-anchor="middle" font-size="11" font-family="Segoe UI, sans-serif">Cold plate interface</text>
        <rect x="294" y="40" width="184" height="88" rx="5" fill="#17212B" stroke="#4DAEFF" stroke-width="1.5"/>
        <text x="386" y="70" fill="#77C2FF" text-anchor="middle" font-size="13" font-family="Segoe UI, sans-serif">SECONDARY STAGE</text>
        <text x="386" y="96" fill="#EAF2FA" text-anchor="middle" font-size="15" font-weight="700" font-family="Segoe UI, sans-serif">Condenser</text>
        <text x="386" y="115" fill="#91A4B7" text-anchor="middle" font-size="11" font-family="Segoe UI, sans-serif">Cold plate interface</text>
        <rect x="548" y="40" width="184" height="88" rx="5" fill="#14252B" stroke="#27C7F5" stroke-width="1.5"/>
        <text x="640" y="70" fill="#79E2FA" text-anchor="middle" font-size="13" font-family="Segoe UI, sans-serif">COOLANT LOOP</text>
        <text x="640" y="96" fill="#EAF2FA" text-anchor="middle" font-size="15" font-weight="700" font-family="Segoe UI, sans-serif">Pump · R385</text>
        <text x="640" y="115" fill="#91A4B7" text-anchor="middle" font-size="11" font-family="Segoe UI, sans-serif">Peltier module</text>
        <rect x="802" y="40" width="188" height="88" rx="5" fill="#17212B" stroke="#19C784" stroke-width="1.5"/>
        <text x="896" y="70" fill="#70DDB1" text-anchor="middle" font-size="13" font-family="Segoe UI, sans-serif">HEAT REJECTION</text>
        <text x="896" y="96" fill="#EAF2FA" text-anchor="middle" font-size="15" font-weight="700" font-family="Segoe UI, sans-serif">Radiator · fans</text>
        <text x="896" y="115" fill="#91A4B7" text-anchor="middle" font-size="11" font-family="Segoe UI, sans-serif">Thermal sink</text>
        <text x="540" y="218" fill="#91A4B7" text-anchor="middle" font-size="11" font-family="Cascadia Code, Consolas, monospace">CONCEPTUAL PATH ONLY · FLOW, PUMP, PELTIER AND FAN OPERATING STATES ARE NOT SENSED</text>
      </svg>
    </div>
    """, unsafe_allow_html=True)


def _plot(frame: pd.DataFrame, title: str, y_title: str = "Temperature (°C)", targets: dict[str, float] | None = None, separate_excursions: bool = True) -> None:
    if frame.empty:
        st.info("No valid, timestamped readings are available for this chart.")
        return
    fig = go.Figure()
    traces = list(frame["trace"].dropna().unique())
    excursion_count = 0
    regular_values = []
    for index, trace in enumerate(traces):
        subset = frame[frame["trace"] == trace]
        q1, q3 = subset["temperature"].quantile([0.25, 0.75])
        spread = q3 - q1
        excursions = subset[(subset["temperature"] < q1 - 1.5 * spread) | (subset["temperature"] > q3 + 1.5 * spread)] if spread > 0 else subset.iloc[0:0]
        regular = subset.drop(index=excursions.index) if separate_excursions else subset
        excursion_count += len(excursions) if separate_excursions else 0
        fig.add_trace(go.Scatter(
            x=regular["time"], y=regular["temperature"], name=trace,
            mode="lines+markers", marker={"size": 4}, line={"width": 2, "color": PALETTE[index % len(PALETTE)]},
            connectgaps=False,
            customdata=regular[["provenance", "sensor_rom"]].fillna("—").to_numpy(),
            hovertemplate="%{x|%Y-%m-%d %H:%M:%S}<br>%{y:.2f} °C<br>Provenance: %{customdata[0]}<br>ROM: %{customdata[1]}<extra>%{fullData.name}</extra>",
        ))
        if separate_excursions and not excursions.empty:
            fig.add_trace(go.Scatter(
                x=excursions["time"], y=excursions["temperature"], name=f"{trace} · preserved excursion",
                mode="markers", yaxis="y2", marker={"size": 9, "symbol": "diamond", "color": PALETTE[index % len(PALETTE)], "line": {"color": "#FFFFFF", "width": 1}},
                customdata=excursions[["provenance", "sensor_rom"]].fillna("—").to_numpy(),
                hovertemplate="%{x|%Y-%m-%d %H:%M:%S}<br>%{y:.2f} °C · preserved value<br>Provenance: %{customdata[0]}<br>ROM: %{customdata[1]}<extra>%{fullData.name}</extra>",
            ))
    for name, target in (targets or {}).items():
        fig.add_hline(y=target, line_dash="dash", line_color="#f6b94b", annotation_text=f"{name} target {target:.0f}°C")
    fig.update_layout(
        title=title, template="plotly_dark", height=340, margin={"l": 12, "r": 18, "t": 52, "b": 12},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(8,13,20,.72)",
        font={"color": "#EAF2FA", "family": "Inter, Segoe UI, sans-serif"},
        legend={"orientation": "h", "y": 1.02, "x": 0},
        xaxis={"title": "Timestamp", "gridcolor": "#233548", "zerolinecolor": "#233548"},
        yaxis={"title": y_title, "gridcolor": "#233548", "zerolinecolor": "#233548", "rangemode": "tozero"},
        yaxis2={"title": "Preserved excursion (°C)", "overlaying": "y", "side": "right", "showgrid": False, "visible": bool(excursion_count)},
        uirevision=title,
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": True, "displaylogo": False, "scrollZoom": True})
    if separate_excursions and excursion_count:
        st.caption(f"{excursion_count} statistically unusual valid reading(s) are preserved as diamond markers on the right-hand scale; hover for exact value, time and provenance. Switch to full range to compare every value on one axis.")


def _choose_range(key: str) -> str:
    return st.selectbox("Time range", list(RANGE_DELTAS), index=2, key=key)


def _filtered_frame(records: list[dict], range_label: str) -> pd.DataFrame:
    return filter_time_range(temperature_history_frame(records), range_label)


def _validity_table(latest: dict | None) -> pd.DataFrame:
    if not latest:
        return pd.DataFrame(columns=["Sensor", "Value", "Validity", "Provenance", "Error"])
    records = latest if isinstance(latest, list) else [latest]
    rows = [{"Sensor": row["sensor"], "Value": _status_temperature(row.get("value")),
             "Validity": row["status"], "Provenance": str(row.get("provenance") or "—").upper(), "Error": row.get("error", "")}
            for row in sensor_status_rows(records)]
    return pd.DataFrame(rows)


def _sensor_status_panel(records: list[dict]) -> None:
    rows = sensor_status_rows(records)
    counts = sensor_status_counts(rows)
    st.subheader("Configured sensor status")
    st.info("The primary heater uses MAX6675 channel 1. No DS18B20 is assigned to the primary heater. Simulation-only data does not establish physical sensor health.")
    columns = st.columns(5)
    for column, status in zip(columns, ("VALID", "INVALID", "STALE", "NOT_CONFIGURED", "SIMULATION_ONLY")):
        column.metric(status.replace("_", " ").title(), counts[status])
    status_frame = pd.DataFrame([{ "Sensor": row["sensor"], "Source": row["source"], "Status": row["status"],
                                   "Value": _status_temperature(row.get("value")),
                                   "Age (s)": round(row["age_seconds"], 1) if row.get("age_seconds") is not None else None,
                                   "Provenance": row.get("provenance") or "—", "Diagnostic": row.get("error", "")}
                                  for row in rows])
    status_frame["Age (s)"] = pd.array(status_frame["Age (s)"], dtype="Float64")
    st.dataframe(status_frame, width="stretch", hide_index=True)
    warnings = configured_sensor_warnings(rows)
    if warnings:
        st.warning("Configured sensor issue: " + "; ".join(warnings))


def _power_scenario() -> dict:
    return estimate_power(
        st.session_state.get("primary_duty", 50),
        st.session_state.get("secondary_duty", 50),
        st.session_state.get("peltier_duty", 0),
        st.session_state.get("cpu_fan_current", 0.0),
        st.session_state.get("brushless_fan_current", 0.0),
        {
            "pump": st.session_state.get("rating_pump_w", 0.0),
            "primary_heater": st.session_state.get("rating_primary_heater_w", 0.0),
            "secondary_heater_1": st.session_state.get("rating_secondary_heater_1_w", 0.0),
            "secondary_heater_2": st.session_state.get("rating_secondary_heater_2_w", 0.0),
            "peltier": st.session_state.get("rating_peltier_w", 0.0),
        },
    )


def _experiment_options(records: list[dict] | None = None) -> tuple[dict[str, dict], list[str]]:
    records = records if records is not None else list_experiments()
    options = {}
    for record in records:
        label = f"#{record['id']} · {record['name']}"
        options[label] = display_experiment(record)
    return options, list(options)


def _experiment_comparison_rows(labels: list[str], options: dict, records: list[dict], sensor_names: list[str]) -> pd.DataFrame:
    rows = []
    for label in labels:
        experiment = options[label]
        associated = experiment_telemetry(records, experiment)
        frame = temperature_history_frame(associated)
        for sensor_name in sensor_names:
            summary = summarize_sensor(frame, sensor_name)
            if summary:
                rows.append({
                    "Experiment": experiment["name"],
                    "Sensor": sensor_name,
                    "Valid samples": summary["count"],
                    "Average (°C)": summary["average"],
                    "Minimum (°C)": summary["minimum"],
                    "Maximum (°C)": summary["maximum"],
                })
    return pd.DataFrame(rows)


def _show_snapshot_messages(snapshot: dict) -> None:
    if snapshot.get("telemetry_error"):
        st.error(f"Telemetry backend unavailable: {snapshot['telemetry_error']}")
        st.caption("The dashboard remains read-only; no hardware actions are issued.")
    if snapshot.get("health_error"):
        st.warning(f"Backend health check unavailable: {snapshot['health_error']}")


def system_overview(snapshot: dict, device_filter: str) -> None:
    page_header("DTC-ITOL · OPERATIONS", "System Overview", "Thermal telemetry, device freshness, and modeled supply headroom.")
    _show_snapshot_messages(snapshot)
    records, latest = snapshot["records"], snapshot["latest"]
    health = snapshot.get("health") or {}
    device_state, device_detail = freshness(latest)
    backend_state = "Online" if not snapshot.get("health_error") and health.get("status") == "ok" else "Unavailable"
    mode = latest.get("mode", "—") if latest else "No data"
    latest_time = latest["timestamp_dt"].astimezone().strftime("%d %b %Y · %H:%M:%S %Z") if latest and latest.get("timestamp_dt") else "Timestamp unavailable"
    sensor_rows = sensor_status_rows(records)
    sensor_counts = sensor_status_counts(sensor_rows)
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("FastAPI backend", backend_state)
    with k2:
        st.metric("ESP32 telemetry", device_state)
        st.caption(device_detail)
    with k3:
        st.metric("Valid physical channels", f"{sensor_counts['VALID']} / 5", help="Five configured physical channels; channel ages are checked against the configured freshness threshold.")
    with k4:
        st.metric("Latest hardware packet", latest_time)

    if latest:
        st.caption(f"Device ID: {latest.get('device_id', 'unknown')} · selected packet mode: {str(mode).upper()}")
        newest_packet = snapshot.get("latest_packet")
        if snapshot.get("latest_hardware") and newest_packet and newest_packet.get("id") != snapshot["latest_hardware"].get("id"):
            st.caption("A newer non-hardware packet remains separate in history; current sensor cards use the latest measured monitoring packet.")
        _heater_status_panel(latest)
        _thermal_system_diagram()
        st.subheader("Latest sensor channels")
        _sensor_cards([s for s in latest.get("sensors", []) if s.get("kind", "temperature") == "temperature"])
    else:
        st.info("Waiting for telemetry. Sensor values remain blank until the backend receives data.")
        _thermal_system_diagram()
    _sensor_status_panel(records)

    st.subheader("Configured supply budgets")
    pwr = _power_scenario()
    budget_cols = st.columns(3)
    with budget_cols[0]:
        st.metric("Combined rated capacity", f"{sum(item['capacity_w'] for item in pwr['supplies']):.0f} W", help="Sum of two 12 V / 10 A supply ratings; not measured output.")
    for column, supply in zip(budget_cols[1:], pwr["supplies"]):
        with column:
            st.metric(supply["name"], f"{supply['capacity_w']:.0f} W rated")
            coverage = "incomplete · load ratings not configured" if supply["unknown_loads"] else "configured estimate"
            st.caption(f"Known scenario subtotal: {supply['known_demand_w']:.1f} W · {coverage}")
    if snapshot.get("telemetry_error") or snapshot.get("health_error"):
        st.error("System warning: backend connection requires attention.")
    if not latest:
        st.warning("System warning: no telemetry has been received.")
    elif configured_sensor_warnings(sensor_rows):
        st.warning("System warning: one or more configured sensor channels require attention.")
    frame = _filtered_frame(records, "24 hours")
    st.subheader("Latest temperature history · 24 hours")
    _plot(frame, "Valid temperature history")


def thermal_monitoring(snapshot: dict, device_filter: str) -> None:
    page_header("SENSOR CHANNELS", "Thermal Monitoring", "Five-second live refresh · invalid, null, and untimestamped readings are excluded from charts.")
    _show_snapshot_messages(snapshot)
    records, latest = snapshot["records"], snapshot["latest"]
    st.info("DS18B20 channels are labeled by verified ROM address. The primary heater uses MAX6675 channel 1; secondary heater 1 control retains MAX6675 channel 2.")
    _sensor_status_panel(records)
    _heater_status_panel(latest)
    if latest:
        st.subheader("All live sensor channels")
        st.caption(f"Packet source: {str(latest.get('mode', 'unknown')).upper()} · channel provenance remains shown per reading.")
        temperature_sensors = [s for s in latest.get("sensors", []) if s.get("kind", "temperature") == "temperature"]
        _sensor_cards(temperature_sensors, "No sensor channels are present in the latest packet.")
    range_label = _choose_range("thermal_time_range")
    frame = _filtered_frame(records, range_label)
    quality_records = _records_in_range(records, range_label)
    quality = history_quality_counts(quality_records)
    st.caption(f"History quality in returned records · valid {quality['valid']} · invalid {quality['invalid']} · null {quality['null']} · non-finite {quality['non_finite']} · not configured {quality['not_configured']}. Zero-degree valid measurements remain visible.")
    scale_mode = st.radio("History scale", ["Operating band + preserved excursions", "Full temperature range"], horizontal=True, key="thermal_scale")
    separate_excursions = scale_mode == "Operating band + preserved excursions"
    st.subheader(f"Separate sensor histories · {range_label}")
    if frame.empty:
        st.info("No valid timestamped readings fall within this range.")
    else:
        names = list(frame["sensor"].unique())
        for offset in range(0, len(names), 2):
            columns = st.columns(2)
            for column, sensor_name in zip(columns, names[offset:offset + 2]):
                subset = frame[frame["sensor"] == sensor_name]
                with column:
                    _plot(subset, sensor_name, separate_excursions=separate_excursions)
                    summary = summarize_sensor(frame, sensor_name)
                    if summary:
                        stats = st.columns(4)
                        stats[0].metric("Min", f"{summary['minimum']:.2f}°C")
                        stats[1].metric("Max", f"{summary['maximum']:.2f}°C")
                        stats[2].metric("Average", f"{summary['average']:.2f}°C")
                        stats[3].metric("Valid samples", summary["count"])
    st.caption("Statistics are calculated from valid readings only. Temperatures and targets are not actuator commands.")


def smart_power_management(snapshot: dict, device_filter: str) -> None:
    page_header("READ-ONLY PLANNING", "Smart Power Management", "Nameplate estimates and scenario planning only · no electrical measurement or ESP32 commands.")
    st.info("No current or power sensor is installed. Enter verified equipment nameplate ratings to calculate estimates; unknown loads remain excluded and supply totals are marked incomplete.")
    with st.expander("Configure nameplate inputs · local session only", expanded=False):
        st.caption("Use each component label/nameplate. Zero means unknown. These values are planning inputs, not measured electrical data.")
        rating_cols = st.columns(3)
        rating_specs = [
            ("Pump nameplate (W)", "rating_pump_w"),
            ("Primary heater nameplate (W)", "rating_primary_heater_w"),
            ("Secondary heater 1 nameplate (W)", "rating_secondary_heater_1_w"),
            ("Secondary heater 2 nameplate (W)", "rating_secondary_heater_2_w"),
            ("Peltier nameplate (W)", "rating_peltier_w"),
        ]
        for index, (label, key) in enumerate(rating_specs):
            with rating_cols[index % len(rating_cols)]:
                st.number_input(label, min_value=0.0, max_value=2000.0, value=0.0, step=1.0, key=key)
        st.caption("ESP32 5 V input demand and buck-converter losses are excluded because their input rating and converter efficiency are not configured.")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.slider("Primary heater scenario duty", 0, 100, 50, 5, key="primary_duty")
    with c2:
        st.slider("Secondary heaters scenario duty", 0, 100, 50, 5, key="secondary_duty")
    with c3:
        st.slider("Peltier scenario duty", 0, 100, 0, 5, key="peltier_duty")
    st.caption("Scenario inputs change calculations on this page only; they do not affect firmware or physical loads.")
    fan_a, fan_b = st.columns(2)
    with fan_a:
        cpu_current = st.number_input("CPU tower fan nameplate current (A) · 0 = unknown", min_value=0.0, max_value=10.0, value=0.0, step=0.1, key="cpu_fan_current")
    with fan_b:
        brushless_current = st.number_input("12V brushless fan nameplate current (A) · 0 = unknown", min_value=0.0, max_value=10.0, value=0.0, step=0.1, key="brushless_fan_current")
    model = estimate_power(
        st.session_state["primary_duty"], st.session_state["secondary_duty"], st.session_state["peltier_duty"],
        cpu_current, brushless_current,
        {
            "pump": st.session_state["rating_pump_w"],
            "primary_heater": st.session_state["rating_primary_heater_w"],
            "secondary_heater_1": st.session_state["rating_secondary_heater_1_w"],
            "secondary_heater_2": st.session_state["rating_secondary_heater_2_w"],
            "peltier": st.session_state["rating_peltier_w"],
        },
    )
    cards = st.columns(2)
    for column, supply in zip(cards, model["supplies"]):
        with column:
            tone = "red" if supply["overload"] else "green"
            estimate_label = "known subtotal" if supply["unknown_loads"] else "configured estimate"
            headroom = "Unknown · incomplete ratings" if supply["unknown_loads"] else f"{supply['headroom_w']:.1f} W modeled headroom"
            st.metric(supply["name"], f"{supply['known_demand_w']:.1f} W {estimate_label} / {supply['capacity_w']:.0f} W rated",
                      delta=headroom, delta_color="inverse" if supply["overload"] else "off")
            pill("KNOWN LOADS EXCEED RATING" if supply["overload"] else ("INCOMPLETE INPUTS" if supply["unknown_loads"] else "CONFIGURED ESTIMATE"),
                 "red" if supply["overload"] else ("amber" if supply["unknown_loads"] else tone))
            if supply["unknown_loads"]:
                st.warning("Unconfigured loads: " + ", ".join(supply["unknown_loads"]) + ". Total demand and headroom are unknown.")
            st.progress(min(1.0, max(0.0, supply["utilization_pct"] / 100.0)),
                        text=f"Known configured subtotal · {supply['utilization_pct']:.1f}% of rated capacity")
    if model["interlock_conflict"]:
        st.warning("Scenario note: secondary heater duty and Peltier duty are both non-zero. This is a planning warning only; no control is sent.")
    st.subheader("Proposed modeled allocation")
    rows = []
    for supply in model["supplies"]:
        for load in supply["loads"]:
            rows.append({
                "Supply": supply["name"], "Assigned load": load["load"],
                "Configured rating": f"{load['rated_w']:.1f} W" if load["rated_w"] is not None else "Not configured",
                "Scenario duty": f"{load['duty_pct']:.0f}%" if load["duty_pct"] is not None else "Not configured",
                "Estimated demand": f"{load['estimated_w']:.1f} W" if load["estimated_w"] is not None else "Excluded · rating unknown",
            })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    figure = go.Figure()
    for item in model["supplies"]:
        figure.add_trace(go.Bar(name="Known configured subtotal · estimate", x=[item["name"]], y=[item["known_demand_w"]], marker_color="#27C7F5"))
        figure.add_trace(go.Bar(name="Supply nameplate capacity", x=[item["name"]], y=[item["capacity_w"]], marker_color="#3B536A"))
    figure.update_layout(barmode="group", template="plotly_dark", height=300, title="Supply nameplate ratings vs known load estimates",
                         yaxis_title="Watts · estimate / nameplate", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(8,13,20,.72)",
                         margin={"l": 12, "r": 12, "t": 50, "b": 12}, showlegend=True,
                         legend={"orientation": "h", "y": 1.08, "x": 0})
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": True, "displaylogo": False, "scrollZoom": True})
    st.caption("Both SMPS units are rated 12 V / 10 A / 120 W. The allocation is a planning model; actual wiring has not been verified here. The chart shows only nameplate inputs configured in this browser session; unconfigured loads are excluded and totals are incomplete. ESP32 input demand and buck losses are excluded. No current or power is measured.")
    st.caption("Fan current inputs calculate an estimate at 12 V; confirm that each fan nameplate specifies 12 V before using that scenario.")


def thermal_analytics(snapshot: dict, device_filter: str) -> None:
    page_header("CALCULATED INDICATORS", "Thermal Analytics", "Comparisons and response trends calculated from valid, timestamped telemetry.")
    _show_snapshot_messages(snapshot)
    records = snapshot["records"]
    primary, secondary = _mapping_controls(records, "analytics")
    range_label = _choose_range("analytics_time_range")
    frame = _filtered_frame(records, range_label)
    targets = {}
    if primary != "Unassigned":
        targets[primary] = SENSOR_TARGETS["primary"]
    if secondary != "Unassigned":
        targets[secondary] = SENSOR_TARGETS["secondary"]
    _plot(frame, f"Thermal response curves · {range_label}", targets=targets)

    if primary != "Unassigned" and secondary != "Unassigned":
        p = frame[frame["sensor"] == primary][["time", "temperature"]].sort_values("time").rename(columns={"temperature": "primary_c"})
        s = frame[frame["sensor"] == secondary][["time", "temperature"]].sort_values("time").rename(columns={"temperature": "secondary_c"})
        if not p.empty and not s.empty:
            comparison = pd.merge_asof(p, s, on="time", direction="nearest", tolerance=pd.Timedelta("5min"))
            comparison = comparison.dropna(subset=["secondary_c"])
            if not comparison.empty:
                comparison["difference_c"] = comparison["primary_c"] - comparison["secondary_c"]
                avg_diff = float(comparison["difference_c"].mean())
                st.metric("Primary − secondary mean difference", f"{avg_diff:+.2f}°C", help="Calculated by nearest timestamp pairing within five minutes.")
                diff_frame = pd.DataFrame({"time": comparison["time"], "temperature": comparison["difference_c"], "sensor": ["Primary − secondary"] * len(comparison), "provenance": ["calculated"] * len(comparison)})
                diff_frame["trace"] = "Calculated temperature difference"
                _plot(diff_frame, "Calculated temperature difference", "Difference (°C)")
            else:
                st.info("No primary/secondary readings are close enough in time to compare.")
        else:
            st.info("Select channels with valid readings to calculate the difference.")
    else:
        st.info("Map both channels in the controls above to compare primary and secondary temperatures.")

    st.subheader("Calculated trend and stability")
    rows = []
    for sensor_name in frame["sensor"].unique() if not frame.empty else []:
        summary = summarize_sensor(frame, sensor_name)
        slope = trend_slope_c_per_hour(frame, sensor_name)
        rows.append({
            "Sensor": sensor_name,
            "Valid samples": summary["count"] if summary else 0,
            "Trend (°C/hour)": f"{slope:+.3f}" if slope is not None else "Not enough time-separated points",
            "Stability (σ °C)": f"{summary['standard_deviation']:.3f}" if summary else "—",
            "Average (°C)": f"{summary['average']:.2f}" if summary else "—",
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption("Trend slope and stability are calculated indicators; they are not direct sensor outputs.")
    else:
        st.info("No valid timestamped data is available for trend calculations.")

    st.subheader("Experiment comparison")
    choices, labels = _experiment_options()
    if labels:
        selected = st.multiselect("Experiments with saved time windows", labels, key="analytics_experiments")
        rows = _experiment_comparison_rows(selected, choices, records, list(frame["sensor"].unique()) if not frame.empty else _all_sensor_names(records))
        if not rows.empty:
            st.dataframe(rows, width="stretch", hide_index=True)
        elif selected:
            st.info("No telemetry in the selected experiment windows was returned by the existing history endpoint.")
    else:
        st.info("Log experiments with start/end timestamps to compare their telemetry windows.")
    st.caption("No cooling efficiency or COP is calculated; the available telemetry does not include verified heat transfer or electrical input measurements.")


def experiment_manager(snapshot: dict, device_filter: str) -> None:
    page_header("RUN RECORDS", "Experiment Manager", "Create time-bounded experiment records and compare returned telemetry without changing the backend schema.")
    _show_snapshot_messages(snapshot)
    now_local = datetime.now().astimezone().replace(tzinfo=None, second=0, microsecond=0)
    with st.form("create_experiment", clear_on_submit=True):
        name = st.text_input("Experiment name")
        description = st.text_input("Description")
        notes = st.text_area("Notes")
        a, b = st.columns(2)
        with a:
            start_at = st.datetime_input("Start timestamp", value=now_local - timedelta(minutes=30))
        with b:
            end_at = st.datetime_input("End timestamp", value=now_local)
        submit = st.form_submit_button("Save experiment")
    if submit:
        if not name.strip():
            st.error("Enter an experiment name.")
        else:
            try:
                experiment_id = log_experiment_details(name, description, notes, start_at.astimezone(), end_at.astimezone())
                st.success(f"Experiment #{experiment_id} saved.")
            except ValueError as exc:
                st.error(str(exc))

    choices, labels = _experiment_options()
    if not labels:
        st.info("No experiments recorded yet. Create one above to start the experiment history.")
        return
    st.subheader("Experiment history")
    history_rows = [{
        "ID": e["id"], "Name": e["name"], "Description": e["description"], "Start": e.get("start_at"),
        "End": e.get("end_at") or "Not recorded", "Notes": e["notes"],
    } for e in choices.values()]
    st.dataframe(pd.DataFrame(history_rows), width="stretch", hide_index=True)
    selected_label = st.selectbox("View experiment", labels, key="view_experiment")
    selected = choices[selected_label]
    st.subheader(f"Results · {selected['name']}")
    if not selected.get("metadata_available"):
        st.info("This is a legacy note without an end timestamp. Telemetry cannot be reliably associated with a bounded experiment window.")
    associated = experiment_telemetry(snapshot["records"], selected)
    st.metric("Telemetry packets in selected window", len(associated))
    if associated:
        frame = temperature_history_frame(associated)
        _plot(frame, "Experiment temperature history")
        names = list(frame["sensor"].unique()) if not frame.empty else []
        rows = _experiment_comparison_rows([selected_label], choices, snapshot["records"], names)
        if not rows.empty:
            st.dataframe(rows, width="stretch", hide_index=True)
        st.caption("Telemetry association is derived locally by comparing timestamps; the API does not store experiment IDs.")
    else:
        st.info("No timestamped telemetry in this experiment window is present in the API's returned history.")

    st.subheader("Compare experiments")
    selected_many = st.multiselect("Select experiments", labels, key="compare_experiments")
    comparison = _experiment_comparison_rows(selected_many, choices, snapshot["records"], _all_sensor_names(snapshot["records"]))
    if not comparison.empty:
        st.dataframe(comparison, width="stretch", hide_index=True)
    elif selected_many:
        st.info("No valid telemetry falls within the selected experiment windows.")


def _telemetry_export(records: list[dict]) -> pd.DataFrame:
    rows = []
    for record in records:
        sensors = record.get("sensors", [])
        if not sensors:
            rows.append({"telemetry_id": record.get("id"), "timestamp": record.get("timestamp"), "device_id": record.get("device_id"), "mode": record.get("mode"), "sensor": None, "sensor_rom": None, "temperature": None, "unit": None, "valid": False, "provenance": None, "sensor_error": "no sensors returned"})
        for sensor in sensors:
            if sensor.get("kind", "temperature") != "temperature":
                continue
            rows.append({
                "telemetry_id": record.get("id"), "timestamp": record.get("timestamp"), "device_id": record.get("device_id"),
                "mode": record.get("mode"), "sensor": sensor_display_name(sensor),
                "sensor_rom": sensor_rom(sensor),
                "temperature": sensor.get("value") if sensor.get("valid") else None,
                "unit": sensor.get("unit"), "valid": bool(sensor.get("valid") and sensor.get("value") is not None),
                "provenance": sensor.get("provenance"), "sensor_error": sensor.get("error"),
            })
    return pd.DataFrame(rows, columns=["telemetry_id", "timestamp", "device_id", "mode", "sensor", "sensor_rom", "temperature", "unit", "valid", "provenance", "sensor_error"])


def _experiment_export(raw_records: list[dict] | None = None) -> pd.DataFrame:
    rows = []
    for raw in raw_records if raw_records is not None else list_experiments():
        record = display_experiment(raw)
        rows.append({
            "experiment_id": record["id"], "experiment_name": record["name"],
            "description": record["description"], "notes": record["notes"],
            "start_at": record.get("start_at"), "end_at": record.get("end_at"), "logged_at": record["created_at"],
        })
    return pd.DataFrame(rows, columns=["experiment_id", "experiment_name", "description", "notes", "start_at", "end_at", "logged_at"])


def _records_in_range(records: list[dict], range_label: str) -> list[dict]:
    delta = RANGE_DELTAS.get(range_label)
    if delta is None:
        return list(records)
    cutoff = datetime.now(timezone.utc) - delta
    return [record for record in records if record.get("timestamp_dt") is not None and record["timestamp_dt"] >= cutoff]


def reports(snapshot: dict, device_filter: str) -> None:
    page_header("DATA EXPORT", "Reports", "CSV exports and summaries from the currently returned telemetry and experiment log.")
    _show_snapshot_messages(snapshot)
    range_label = _choose_range("reports_time_range")
    report_records = _records_in_range(snapshot["records"], range_label)
    st.caption(f"Report time window · {range_label}")
    telemetry = _telemetry_export(report_records)
    raw_experiments = list_experiments()
    experiments = _experiment_export(raw_experiments)
    left, right = st.columns(2)
    with left:
        st.metric("Telemetry rows returned", len(telemetry))
        st.download_button("Download telemetry CSV", telemetry.to_csv(index=False).encode("utf-8"),
                           file_name="dtc_itol_telemetry.csv", mime="text/csv", key="report_telemetry_csv")
    with right:
        st.metric("Experiment records", len(experiments))
        st.download_button("Download experiment log CSV", experiments.to_csv(index=False).encode("utf-8"),
                           file_name="dtc_itol_experiments.csv", mime="text/csv", key="report_experiments_csv")

    st.subheader("Temperature summary · valid readings only")
    frame = temperature_history_frame(report_records)
    summary_rows = []
    for name in frame["sensor"].unique() if not frame.empty else []:
        summary = summarize_sensor(frame, name)
        summary_rows.append({"Sensor": name, "Valid samples": summary["count"], "Min °C": summary["minimum"], "Max °C": summary["maximum"], "Average °C": summary["average"], "Provenance": ", ".join(sorted(frame.loc[frame["sensor"] == name, "provenance"].unique()))})
    if summary_rows:
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)
    else:
        st.info("No valid timestamped temperatures to summarize.")

    st.subheader("Power estimate summary")
    power = _power_scenario()
    for column, supply in zip(st.columns(2), power["supplies"]):
        with column:
            st.metric(f"{supply['name']} · rated capacity", f"{supply['capacity_w']:.0f} W")
            if supply["unknown_loads"]:
                st.caption(f"Known configured subtotal: {supply['known_demand_w']:.1f} W · total demand unavailable")
                st.caption("Unconfigured: " + ", ".join(supply["unknown_loads"]))
            else:
                st.caption(f"Configured scenario estimate: {supply['known_demand_w']:.1f} W · not measured")
    st.caption("Power values use user-entered local nameplate inputs; no electrical measurements are present.")

    st.subheader("Experiment summaries")
    choices, labels = _experiment_options(raw_experiments)
    rows = []
    for label in labels:
        experiment = choices[label]
        associated = experiment_telemetry(snapshot["records"], experiment)
        valid_count = sum(1 for record in associated for sensor in record["sensors"] if sensor.get("valid") and sensor.get("value") is not None)
        rows.append({"Experiment": experiment["name"], "Start": experiment.get("start_at"), "End": experiment.get("end_at") or "Not recorded", "Telemetry packets": len(associated), "Valid sensor samples": valid_count})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("No experiment records are available.")
    st.caption("Telemetry exports retain provenance and validity. Invalid/null values remain blank. The current project has no PDF-report dependency.")


def system_diagnostics(snapshot: dict, device_filter: str) -> None:
    page_header("CONNECTIVITY & FAULTS", "System Diagnostics", "Current API and telemetry status. The backend does not store ESP32 delivery logs or a system-event journal.")
    _show_snapshot_messages(snapshot)
    latest = snapshot["latest"]
    _heater_status_panel(latest)
    health = snapshot.get("health") or {}
    state, detail = freshness(latest)
    backend_ok = not snapshot.get("health_error") and health.get("status") == "ok"
    columns = st.columns(4)
    columns[0].metric("ESP32 packet freshness", state)
    columns[1].metric("FastAPI health", "Healthy" if backend_ok else "Unavailable")
    columns[2].metric("Last telemetry", latest.get("timestamp") if latest else "None")
    columns[3].metric("Actuator control", health.get("actuator_control", "Not reported"))
    st.caption(detail)
    if snapshot.get("telemetry_error"):
        st.error(f"Telemetry delivery/read error: {snapshot['telemetry_error']}")
    if snapshot.get("health_error"):
        st.error(f"Backend health error: {snapshot['health_error']}")

    st.subheader("Latest sensor validity")
    table = _validity_table(snapshot["records"])
    if table.empty:
        st.info("No sensor diagnostics until telemetry arrives.")
    else:
        st.dataframe(table, width="stretch", hide_index=True)
        fault_rows = table[table["Validity"].isin(["INVALID", "STALE"])]
        if not fault_rows.empty:
            st.warning("Configured sensor channels needing attention: " + ", ".join(fault_rows["Sensor"].tolist()))

    st.subheader("Current system warnings")
    warnings = []
    if not backend_ok:
        warnings.append("FastAPI health is not confirmed.")
    if snapshot.get("telemetry_error"):
        warnings.append("Telemetry history request failed.")
    if latest is None:
        warnings.append("No telemetry packet is available.")
    elif state == "Stale":
        warnings.append("Latest telemetry is stale; ESP32 connectivity is inferred from packet age.")
    warnings.extend(configured_sensor_warnings(sensor_status_rows(snapshot["records"])))
    if latest:
        warnings.extend(f"{sensor.get('name', 'Heater')}: {sensor.get('error') or 'heater state fault'}"
                        for sensor in latest.get("sensors", [])
                        if sensor.get("kind") == "heater_state" and str(sensor.get("state", "")).upper() == "FAULT")
    if warnings:
        for warning in warnings:
            st.warning(warning)
    else:
        st.success("No current API or sensor validity warnings in the returned snapshot.")
    st.info("HTTP telemetry delivery errors and historical system events are not exposed by the current backend API; they are not inferred here.")
