from dashboard import experiment_store
from dashboard import api_client
from dashboard import data_access
from dashboard.analytics import experiment_telemetry, filter_time_range, summarize_sensor
from dashboard.power_model import estimate_power
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import pandas as pd
from dashboard.presentation import sensor_display_name, sensor_provenance_label, temperature_history_frame
from dashboard.sensor_status import configured_sensor_warnings, sensor_status_counts, sensor_status_rows


def test_experiment_log_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_store, "DB_PATH", tmp_path / "experiments.sqlite3")
    experiment_id = experiment_store.log_experiment("  thermal baseline  ", "  startup observations  ")
    records = experiment_store.list_experiments()
    assert records[0]["id"] == experiment_id
    assert records[0]["name"] == "thermal baseline"
    assert records[0]["notes"] == "startup observations"


def test_experiment_name_is_required():
    try:
        experiment_store.log_experiment("  ", "notes")
    except ValueError as exc:
        assert "name is required" in str(exc)
    else:
        raise AssertionError("blank experiment names should be rejected")


def test_dashboard_api_client_reports_http_errors():
    transport = httpx.MockTransport(lambda request: httpx.Response(503, json={"detail": "offline"}))
    result, error = api_client.fetch_json("http://backend", "/api/v1/telemetry", transport=transport)
    assert result is None
    assert "503" in error


def test_dashboard_api_client_reports_connection_errors():
    def fail_connection(request):
        raise httpx.ConnectError("backend unreachable", request=request)

    result, error = api_client.fetch_json(
        "http://backend", "/api/v1/telemetry", transport=httpx.MockTransport(fail_connection)
    )
    assert result is None
    assert "backend unreachable" in error


def test_dashboard_display_data_preserves_simulated_provenance():
    records = [{
        "id": 1,
        "device_id": "dtc-itol-sim-01",
        "mode": "simulation",
        "timestamp": "2026-11-14T10:00:00+00:00",
        "sensors": [{
            "name": "ds18b20_1", "value": 27.25, "unit": "°C", "valid": True,
            "provenance": "simulated", "error": None,
        }],
    }]

    frame = temperature_history_frame(records)
    assert frame.iloc[0]["trace"] == "ds18b20_1 · simulated"
    assert frame.iloc[0]["temperature"] == 27.25
    assert sensor_provenance_label(records[0]["sensors"][0]) == "SIMULATED · valid"


def test_null_invalid_and_missing_timestamp_readings_are_not_plotted():
    records = [
        {
            "id": 1, "timestamp": "2026-11-14T10:00:00+00:00",
            "sensors": [
                {"name": "valid", "value": 0.0, "valid": True, "provenance": "measured"},
                {"name": "null", "value": None, "valid": False, "provenance": "measured"},
                {"name": "invalid numeric", "value": 99.0, "valid": False, "provenance": "measured"},
            ],
        },
        {"id": 2, "timestamp": None, "sensors": [{"name": "untimed", "value": 20.0, "valid": True, "provenance": "simulated"}]},
    ]
    normalized = data_access.normalize_records(records)
    frame = temperature_history_frame(normalized)
    assert frame["sensor"].tolist() == ["valid"]
    assert frame["temperature"].tolist() == [0.0]
    assert not normalized[0]["sensors"][1]["valid"]
    assert normalized[1]["timestamp_dt"] is None


def test_heater_status_extension_is_not_plotted_as_a_temperature_sensor():
    records = [{
        "timestamp": "2026-11-14T10:00:00+00:00",
        "sensors": [
            {"name": "tc_primary", "value": 25.0, "valid": True, "provenance": "measured"},
            {"name": "primary_mk8", "kind": "heater_state", "value": 25.0,
             "valid": True, "provenance": "measured", "target_c": 150,
             "state": "DISABLED", "control_mode": "MONITOR_ONLY", "progress_pct": 16.7},
        ],
    }]
    frame = temperature_history_frame(data_access.normalize_records(records))
    assert frame["sensor"].tolist() == ["tc_primary"]


def test_verified_sensor_labels_require_the_expected_rom_address():
    assert sensor_display_name({
        "name": "ds18b20_secondary_heater_2", "sensor_rom": "28206B79000000F2"
    }) == "Secondary heater 2 monitoring · DS18B20 · ROM …00F2"
    assert sensor_display_name({
        "name": "ds18b20_coolant", "sensor_rom": "28206B79000000F2"
    }) == "Secondary heater 2 monitoring · DS18B20 · ROM …00F2 · declared role mismatch"
    assert sensor_display_name({
        "name": "ds18b20_coolant", "sensor_rom": "28741C78000000B3"
    }) == "Coolant temperature · DS18B20 · ROM …00B3"
    assert sensor_display_name({
        "name": "ds18b20_secondary_heater", "sensor_rom": "288B1F78000000AB"
    }) == "Secondary heater 1 monitoring · DS18B20 · ROM …00AB"
    assert sensor_display_name({"name": "thermocouple_primary"}) == "Primary heater · MAX6675 channel 1"
    assert sensor_display_name({"name": "thermocouple_secondary"}) == "Secondary heater 1 control · MAX6675 channel 2"


def test_verified_mapping_label_is_used_in_temperature_history():
    records = [{
        "timestamp": "2026-11-14T10:00:00+00:00",
        "sensors": [{
            "name": "ds18b20_coolant", "sensor_rom": "28741C78000000B3",
            "value": 28.5, "valid": True, "provenance": "measured",
        }],
    }]
    frame = temperature_history_frame(records)
    assert frame.iloc[0]["sensor"] == "Coolant temperature · DS18B20 · ROM …00B3"


def test_latest_simulation_does_not_replace_latest_measured_hardware_packet():
    measured = {"id": 10, "mode": "monitoring", "sensors": [{"provenance": "measured"}]}
    newer_simulation = {"id": 11, "mode": "simulation", "sensors": [{"provenance": "simulated"}]}
    assert data_access.latest_hardware_record([newer_simulation, measured]) is measured
    assert data_access.latest_hardware_record([newer_simulation]) is None
    assert data_access.freshness(newer_simulation)[0] == "No hardware data"


def test_device_freshness_distinguishes_connected_stale_and_offline():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    measured = {"mode": "monitoring", "timestamp_dt": now - timedelta(seconds=5),
                "sensors": [{"kind": "temperature", "provenance": "measured"}]}
    assert data_access.freshness(measured, now)[0] == "Connected"
    measured["timestamp_dt"] = now - timedelta(seconds=30)
    assert data_access.freshness(measured, now)[0] == "Stale"
    measured["timestamp_dt"] = now - timedelta(seconds=90)
    assert data_access.freshness(measured, now)[0] == "Offline"


def test_power_model_requires_nameplate_inputs_and_keeps_unknown_loads_unconfigured():
    estimate = estimate_power(50, 50, 0, None, None)
    smps1, smps2 = estimate["supplies"]
    assert smps1["capacity_w"] == 120
    assert smps1["known_demand_w"] == 0
    assert smps1["unknown_loads"] == ["R385 pump", "Primary cartridge heater", "Peltier module"]
    assert smps2["known_demand_w"] == 0
    assert smps2["unknown_loads"] == ["Secondary cartridge heater 1", "Secondary cartridge heater 2", "CPU tower fan", "12V brushless fan"]
    assert estimate["is_estimate"] is True
    assert estimate_power(50, 20, 10, 0, 0)["interlock_conflict"] is True

    configured = estimate_power(50, 50, 0, None, None, {
        "pump": 24, "primary_heater": 40, "secondary_heater_1": 40,
        "secondary_heater_2": 40, "peltier": 72,
    })
    assert configured["supplies"][0]["known_demand_w"] == 44
    assert configured["supplies"][1]["known_demand_w"] == 40
    assert configured["supplies"][0]["unknown_loads"] == []


def test_experiment_metadata_uses_existing_notes_column_and_reads_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_store, "DB_PATH", tmp_path / "experiments.sqlite3")
    start = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 14, 11, 0, tzinfo=timezone.utc)
    experiment_store.log_experiment("Timed run", "legacy notes")
    experiment_store.log_experiment_details("Metadata run", "baseline", "careful notes", start, end)
    rows = experiment_store.list_experiments()
    legacy = experiment_store.display_experiment(next(row for row in rows if row["name"] == "Timed run"))
    timed = experiment_store.display_experiment(next(row for row in rows if row["name"] == "Metadata run"))
    assert legacy["notes"] == "legacy notes"
    assert legacy["metadata_available"] is False
    assert timed["description"] == "baseline"
    assert timed["notes"] == "careful notes"
    assert timed["start_at"] == start.isoformat()
    assert timed["end_at"] == end.isoformat()
    with sqlite3.connect(experiment_store.DB_PATH) as connection:
        columns = {row[1] for row in connection.execute("pragma table_info(experiments)")}
    assert columns == {"id", "name", "notes", "created_at"}


def test_experiment_telemetry_association_requires_timestamped_window():
    record = {"timestamp_dt": datetime(2026, 11, 14, 10, 30, tzinfo=timezone.utc)}
    window = {"start_at": "2026-11-14T10:00:00+00:00", "end_at": "2026-11-14T11:00:00+00:00"}
    assert experiment_telemetry([record], window) == [record]
    assert experiment_telemetry([record], {"start_at": "2026-11-14T10:00:00+00:00"}) == []


def test_dashboard_uses_live_api_and_only_displays_measured_monitoring_packets():
    app_source = (Path(__file__).parents[1] / "dashboard" / "app.py").read_text(encoding="utf-8").lower()
    assert "load_snapshot" in app_source
    assert "measured device telemetry" in app_source
    assert "dashboard.simulation" not in app_source
    assert "@st.fragment(run_every=" in app_source
    assert "actuator feedback circuit" in app_source
    assert "set_page_config" in app_source
    assert "st.sidebar" not in app_source


def test_live_monitor_history_excludes_simulation_and_non_measured_samples():
    records = [
        {"mode": "simulation", "sensors": [{"name": "x", "value": 99, "valid": True, "provenance": "simulated"}]},
        {"mode": "monitoring", "sensors": [
            {"name": "measured", "value": 25, "valid": True, "provenance": "measured"},
            {"name": "estimated", "value": 30, "valid": True, "provenance": "estimated"},
            {"name": "heater", "kind": "heater_state", "value": 25, "valid": True, "provenance": "measured"},
        ]},
    ]
    selected = data_access.measured_monitoring_records(records)
    assert len(selected) == 1
    assert [row["name"] for row in selected[0]["sensors"]] == ["measured"]


def test_live_dashboard_renders_measured_api_packet(monkeypatch):
    from datetime import datetime, timezone
    from streamlit.testing.v1 import AppTest

    stamp = datetime.now(timezone.utc).isoformat()
    sensors = [
        {"name": "thermocouple_primary", "value": 31.0, "valid": True, "provenance": "measured"},
        {"name": "thermocouple_secondary", "value": 32.0, "valid": True, "provenance": "measured"},
        {"name": "ds18b20_secondary_heater", "sensor_rom": "288B1F78000000AB", "value": 33.0, "valid": True, "provenance": "measured"},
        {"name": "ds18b20_secondary_heater_2", "sensor_rom": "28206B79000000F2", "value": 34.0, "valid": True, "provenance": "measured"},
        {"name": "ds18b20_coolant", "sensor_rom": "28741C78000000B3", "value": 35.0, "valid": True, "provenance": "measured"},
        {"name": "primary_mk8", "kind": "heater_state", "value": 31.0, "valid": True,
         "provenance": "measured", "state": "DISABLED", "intended_state": "OFF", "control_mode": "MONITOR_ONLY"},
    ]
    record = {"id": 1, "device_id": "dtc-itol-esp32-01", "mode": "monitoring", "timestamp": stamp, "sensors": sensors}

    def fake_fetch(api_base, path, params=None, *, transport=None):
        if path == "/api/v1/telemetry":
            return [record], None
        if path == "/health":
            return {"status": "ok", "actuator_control": "disabled"}, None
        if path == "/api/device/status":
            return {"state": "CONNECTED", "controller_mode": "MONITOR_ONLY",
                    "telemetry_age_seconds": 0.1, "wifi_rssi_dbm": -38,
                    "ip_address": "192.168.1.10"}, None
        return None, "unexpected endpoint"

    monkeypatch.setattr(api_client, "fetch_json", fake_fetch)
    data_access.load_snapshot.clear()
    app = AppTest.from_file(str(Path(__file__).parents[1] / "dashboard" / "app.py")).run(timeout=15)
    assert not app.exception
    rendered = " ".join(element.value for element in app.markdown if isinstance(element.value, str))
    assert "MEASURED DEVICE TELEMETRY" in rendered
    assert "SIMULATION" not in rendered
    for value in ("31.00 °C", "32.00 °C", "33.00 °C", "34.00 °C", "35.00 °C"):
        assert value in rendered


def test_primary_and_secondary_reach_threshold_then_hold_three_simulated_seconds():
    from dashboard.simulation import advance_simulation, create_simulation

    state = create_simulation(0.0)
    wall = 0.0
    while state["secondary"]["phase"] == "HEATING":
        wall += 0.1
        advance_simulation(state, wall)
    secondary = state["secondary"]
    assert secondary["temperature_c"] == 100.0
    assert secondary["phase"] == "HOLD"
    assert 2.8 < secondary["hold_remaining_s"] < 3.0
    hold_left = state["secondary"]["hold_remaining_s"]
    advance_simulation(state, wall + hold_left - 0.1)
    assert state["secondary"]["phase"] == "HOLD"
    assert abs(state["secondary"]["hold_remaining_s"] - 0.1) < 1e-7
    wall += hold_left
    advance_simulation(state, wall)
    assert state["secondary"]["phase"] == "FAN RESPONSE"
    assert any("Secondary target 100°C reached" in event["message"] for event in state["events"])
    secondary_hold_event = next(event for event in state["events"] if "Secondary target 100°C reached" in event["message"])
    secondary_fan_event = next(event for event in state["events"] if "Secondary threshold dwell complete" in event["message"])
    assert abs((secondary_fan_event["elapsed"] - secondary_hold_event["elapsed"]) - 3.0) < 1e-7

    while state["primary"]["phase"] == "HEATING":
        wall += 0.1
        advance_simulation(state, wall)
    primary = state["primary"]
    assert primary["temperature_c"] == 150.0
    assert primary["phase"] == "HOLD"
    assert 2.8 < primary["hold_remaining_s"] < 3.0
    wall += 3.0
    advance_simulation(state, wall)
    assert state["primary"]["phase"] == "FAN RESPONSE"
    assert any("Primary target 150°C reached" in event["message"] for event in state["events"])
    primary_hold_event = next(event for event in state["events"] if "Primary target 150°C reached" in event["message"])
    primary_fan_event = next(event for event in state["events"] if "Primary threshold dwell complete" in event["message"])
    assert abs((primary_fan_event["elapsed"] - primary_hold_event["elapsed"]) - 3.0) < 1e-7


def test_fan_response_and_cooling_are_gradual():
    from dashboard.simulation import advance_simulation, create_simulation

    state = create_simulation(0.0)
    wall = 0.0
    while state["primary"]["phase"] == "HEATING":
        wall += 0.1
        advance_simulation(state, wall)
    advance_simulation(state, wall + 9.0)
    rpm_at_fan_response = state["cpu_fan_rpm"]
    assert rpm_at_fan_response > 500.0
    assert state["primary"]["phase"] == "COOLING"
    secondary_rpm_at_cooling = state["secondary_fan_rpm"]
    advance_simulation(state, wall + 34.0)
    assert state["primary"]["phase"] == "COOLING"
    assert 500.0 < state["cpu_fan_rpm"] < rpm_at_fan_response
    assert state["secondary_fan_rpm"] < secondary_rpm_at_cooling
    temperature_before = state["primary"]["temperature_c"]
    advance_simulation(state, wall + 35.0)
    assert state["primary"]["temperature_c"] < temperature_before
    assert temperature_before - state["primary"]["temperature_c"] < 1.0
    assert state["primary"]["temperature_c"] > 25.0


def test_pause_resume_reset_and_simulation_speeds():
    from dashboard.simulation import advance_simulation, create_simulation

    one_x = create_simulation(0.0)
    advance_simulation(one_x, 1.0)
    assert abs(one_x["elapsed"] - 1.0) < 1e-7
    one_x["paused"] = True
    advance_simulation(one_x, 20.0)
    assert abs(one_x["elapsed"] - 1.0) < 1e-7
    one_x["paused"] = False
    one_x["last_wall"] = 20.0
    one_x["speed"] = 2.0
    advance_simulation(one_x, 21.0)
    assert abs(one_x["elapsed"] - 3.0) < 1e-7
    one_x["speed"] = 5.0
    advance_simulation(one_x, 22.0)
    assert abs(one_x["elapsed"] - 8.0) < 1e-7
    reset = create_simulation(22.0)
    assert reset["elapsed"] == 0.0 and reset["primary"]["temperature_c"] == 25.0


def _measured_record(timestamp, sensors):
    return {"timestamp": timestamp, "sensors": sensors}


def test_unassigned_primary_ds18b20_is_not_configured_and_not_a_fault():
    sensors = [{
        "name": "thermocouple_primary", "value": 23.0, "valid": True, "provenance": "measured",
    }, {
        "name": "thermocouple_secondary", "value": 23.0, "valid": True, "provenance": "measured",
    }, {
        "name": "ds18b20_rom_288B1F78000000AB", "value": 23.0, "valid": True, "provenance": "measured",
    }, {
        "name": "ds18b20_rom_28206B79000000F2", "value": 23.0, "valid": True, "provenance": "measured",
    }, {
        "name": "ds18b20_rom_28741C78000000B3", "value": 23.0, "valid": True, "provenance": "measured",
    }, {
        "name": "ds18b20_primary_unassigned", "value": None, "valid": False,
        "provenance": "measured", "error": "location_mapping_unconfigured",
    }]
    rows = sensor_status_rows([_measured_record("2026-11-16T10:00:00+00:00", sensors)], now=datetime(2026, 11, 16, 10, 0, 1, tzinfo=timezone.utc))
    unused = next(row for row in rows if row["status"] == "NOT_CONFIGURED")
    assert "Primary heater uses MAX6675 channel 1" == unused["source"]
    assert sensor_status_counts(rows)["INVALID"] == 0
    assert sensor_status_counts(rows)["VALID"] == 5
    assert all("NOT CONFIGURED" not in warning for warning in configured_sensor_warnings(rows))


def test_configured_disconnected_sensor_is_invalid_and_stale_sensor_is_distinguished():
    base = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    identities = [
        ("thermocouple_primary", None), ("thermocouple_secondary", None),
        ("ds18b20_rom_288B1F78000000AB", None), ("ds18b20_rom_28206B79000000F2", None),
        ("ds18b20_rom_28741C78000000B3", None),
    ]
    valid_sensors = [{"name": n, "value": 23.0, "valid": True, "provenance": "measured"} for n, _ in identities]
    valid_sensors[2] = {"name": identities[2][0], "value": None, "valid": False, "provenance": "measured", "error": "disconnected"}
    rows = sensor_status_rows([_measured_record(base.isoformat(), valid_sensors)], now=base + timedelta(seconds=10))
    assert next(r for r in rows if r["identity"] == "ds18b20:288B1F78000000AB")["status"] == "INVALID"
    assert all(r["status"] == "STALE" for r in rows if r["configured"] and r["identity"] != "ds18b20:288B1F78000000AB")
    warnings = configured_sensor_warnings(rows)
    assert any("Secondary heater 1 monitoring" in warning for warning in warnings)
    assert all("NOT CONFIGURED" not in warning for warning in warnings)


def test_simulation_data_does_not_create_physical_sensor_fault_warnings():
    records = [{"mode": "simulation", "timestamp": "2026-11-16T10:00:00+00:00", "sensors": [
        {"name": "thermocouple_primary", "value": 25.0, "valid": True, "provenance": "simulated"},
    ]}]
    rows = sensor_status_rows(records, now=datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc))
    assert sensor_status_counts(rows)["SIMULATION_ONLY"] == 5
    assert not configured_sensor_warnings(rows)


def test_current_f2_temperature_validity_does_not_erase_independent_latched_heater_fault():
    now = datetime(2026, 11, 16, 16, 4, 7, tzinfo=timezone.utc)
    packet = _measured_record(now.isoformat(), [
        {"name": "ds18b20_secondary_heater_2", "sensor_rom": "28206B79000000F2",
         "value": 22.56, "valid": True, "provenance": "measured", "kind": "temperature"},
        {"name": "secondary_heater_2", "sensor_rom": "28206B79000000F2",
         "value": 22.56, "valid": True, "provenance": "measured", "kind": "heater_state",
         "state": "FAULT", "error": "invalid_sensor_reading", "control_mode": "MONITOR_ONLY"},
    ])
    packet["mode"] = "monitoring"
    f2 = next(row for row in sensor_status_rows([packet], now=now)
              if row["identity"] == "ds18b20:28206B79000000F2")
    heater = next(sensor for sensor in packet["sensors"] if sensor.get("kind") == "heater_state")
    assert f2["status"] == "VALID"
    assert heater["valid"] is True
    assert heater["state"] == "FAULT"
    assert heater["error"] == "invalid_sensor_reading"


def test_missing_configured_rom_sample_is_invalid_not_silently_mapped():
    now = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    sensors = [
        {"name": "thermocouple_primary", "value": 23, "valid": True, "provenance": "measured"},
        {"name": "thermocouple_secondary", "value": 23, "valid": True, "provenance": "measured"},
        {"name": "ds18b20_rom_288B1F78000000AB", "value": 23, "valid": True, "provenance": "measured"},
        {"name": "ds18b20_rom_28741C78000000B3", "value": 23, "valid": True, "provenance": "measured"},
    ]
    rows = sensor_status_rows([_measured_record(now.isoformat(), sensors)], now=now)
    missing_f2 = next(row for row in rows if row["identity"] == "ds18b20:28206B79000000F2")
    assert missing_f2["status"] == "INVALID"


def test_history_excludes_invalid_null_nonfinite_but_preserves_zero_and_high_values():
    readings = [
        {"name": "thermocouple_primary", "value": 0.0, "valid": True, "provenance": "measured"},
        {"name": "thermocouple_primary", "value": 390.0, "valid": True, "provenance": "measured"},
        {"name": "thermocouple_primary", "value": None, "valid": False, "provenance": "measured"},
        {"name": "thermocouple_primary", "value": float("inf"), "valid": True, "provenance": "measured"},
        {"name": "thermocouple_primary", "value": 900.0, "valid": False, "provenance": "measured"},
    ]
    frame = temperature_history_frame([_measured_record("2026-11-16T10:00:00+00:00", readings)])
    assert sorted(frame["temperature"].tolist()) == [0.0, 390.0]


def test_rom_mapping_identity_ignores_sensor_discovery_order_and_max_roles_remain_distinct():
    roms = ["288B1F78000000AB", "28741C78000000B3", "28206B79000000F2"]
    sensors = [{"name": f"ds18b20_rom_{rom}", "value": 25, "valid": True, "provenance": "measured"} for rom in reversed(roms)]
    frame = temperature_history_frame([_measured_record("2026-11-16T10:00:00+00:00", sensors)])
    assert set(frame["identity"]) == {f"ds18b20:{rom}" for rom in roms}
    assert "Secondary heater 1 control · MAX6675 channel 2" == sensor_display_name({"name": "thermocouple_secondary", "provenance": "measured"})
    assert "Primary heater · MAX6675 channel 1" == sensor_display_name({"name": "thermocouple_primary", "provenance": "measured"})


def test_time_range_filters_history_correctly():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    frame = pd.DataFrame({"time": pd.to_datetime([now - timedelta(minutes=30), now - timedelta(hours=2), now - timedelta(days=2)]), "temperature": [20, 21, 22]})
    assert filter_time_range(frame, "1 hour", now)["temperature"].tolist() == [20]
    assert filter_time_range(frame, "6 hours", now)["temperature"].tolist() == [20, 21]
    assert len(filter_time_range(frame, "7 days", now)) == 3
