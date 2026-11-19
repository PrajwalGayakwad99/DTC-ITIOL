from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.schemas import TelemetryIn
from backend.app.simulator import create_simulated_telemetry
from backend.app.store import TelemetryStore


def sample_payload():
    return TelemetryIn.model_validate(
        {
            "device_id": "esp32-sim-01",
            "mode": "simulation",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sensors": [
                {
                    "name": "simulated_probe_1",
                    "value": 42.5,
                    "unit": "°C",
                    "valid": True,
                    "provenance": "simulated",
                }
            ],
        }
    )


def test_sqlite_round_trip(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    saved = store.add(sample_payload())
    result = store.latest(limit=10)
    assert len(result) == 1
    assert result[0].id == saved.id
    assert result[0].sensors[0].value == 42.5
    assert result[0].sensors[0].provenance.value == "simulated"


def test_heater_status_extension_round_trips_in_existing_sqlite_sensor_json(tmp_path):
    store = TelemetryStore(tmp_path / "heater-status.sqlite3")
    packet = sample_payload().model_dump(mode="json")
    packet["sensors"].append({
        "name": "primary_mk8", "kind": "heater_state", "value": 24.0,
        "unit": "°C", "valid": True, "provenance": "measured",
        "target_c": 150.0, "state": "DISABLED", "intended_state": "ON",
        "control_mode": "DRY_RUN", "progress_pct": 16.0, "error": None,
        "sensor_rom": "28206B79000000F2",
    })
    saved = store.add(TelemetryIn.model_validate(packet))
    history = store.latest()
    heater = next(sensor for sensor in history[0].sensors if sensor.kind == "heater_state")
    assert history[0].id == saved.id
    assert heater.target_c == 150.0
    assert heater.state == "DISABLED"
    assert heater.intended_state == "ON"
    assert heater.control_mode == "DRY_RUN"
    assert heater.sensor_rom == "28206B79000000F2"


def test_wifi_and_controller_metadata_round_trip_without_changing_sensor_json(tmp_path):
    store = TelemetryStore(tmp_path / "device-metadata.sqlite3")
    payload = sample_payload().model_dump(mode="json")
    payload.update({
        "controller_mode": "MONITOR_ONLY", "wifi_connected": True,
        "wifi_rssi_dbm": -42, "ip_address": "192.168.1.10",
    })
    saved = store.add(TelemetryIn.model_validate(payload))
    result = store.latest()[0]
    assert saved.controller_mode.value == "MONITOR_ONLY"
    assert result.wifi_connected is True
    assert result.wifi_rssi_dbm == -42
    assert result.ip_address == "192.168.1.10"


def test_existing_sqlite_schema_is_migrated_additively(tmp_path):
    import sqlite3

    db_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("""CREATE TABLE telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL,
            timestamp TEXT NOT NULL, mode TEXT NOT NULL, sensors_json TEXT NOT NULL
        )""")
    store = TelemetryStore(db_path)
    assert store.add(sample_payload()).id == 1
    assert store.latest()[0].controller_mode is None


def test_duplicate_packets_are_idempotent_and_out_of_order_rows_stay_chronological(tmp_path):
    store = TelemetryStore(tmp_path / "ordering.sqlite3")
    base = sample_payload().model_dump(mode="json")
    base["timestamp"] = "2026-11-19T10:00:03+00:00"
    newest = store.add(TelemetryIn.model_validate(base))
    duplicate = store.add(TelemetryIn.model_validate(base))
    older = dict(base, timestamp="2026-11-19T10:00:00+00:00")
    store.add(TelemetryIn.model_validate(older))
    rows = store.latest(limit=10)
    assert duplicate.id == newest.id
    assert len(rows) == 2
    assert [row.timestamp.isoformat() for row in rows] == [
        "2026-11-19T10:00:03+00:00", "2026-11-19T10:00:00+00:00"
    ]


def test_legacy_telemetry_without_sensor_rom_remains_valid():
    parsed = sample_payload()
    assert parsed.sensors[0].sensor_rom is None


def test_sensor_rom_metadata_requires_sixteen_hex_characters():
    payload = sample_payload().model_dump(mode="json")
    payload["sensors"][0]["sensor_rom"] = "not-a-rom"
    with pytest.raises(ValueError, match="16 hexadecimal characters"):
        TelemetryIn.model_validate(payload)


def test_rejects_naive_timestamp():
    payload = sample_payload().model_dump(mode="json")
    payload["timestamp"] = "2026-11-14T12:00:00"
    with pytest.raises(ValueError):
        TelemetryIn.model_validate(payload)


def test_rejects_valid_reading_without_value():
    payload = sample_payload().model_dump(mode="json")
    payload["sensors"][0]["value"] = None
    with pytest.raises(ValueError):
        TelemetryIn.model_validate(payload)


def test_invalid_sensor_can_be_recorded_as_missing_value():
    payload = sample_payload().model_dump(mode="json")
    payload["sensors"][0].update(value=None, valid=False, error="disconnected")
    parsed = TelemetryIn.model_validate(payload)
    assert parsed.sensors[0].value is None
    assert parsed.sensors[0].valid is False


def test_rejects_sensor_with_missing_value_field():
    payload = sample_payload().model_dump(mode="json")
    del payload["sensors"][0]["value"]
    with pytest.raises(ValueError):
        TelemetryIn.model_validate(payload)


def test_rejects_out_of_range_temperature():
    payload = sample_payload().model_dump(mode="json")
    payload["sensors"][0]["value"] = 2000
    with pytest.raises(ValueError):
        TelemetryIn.model_validate(payload)


def test_simulator_generates_five_explicitly_simulated_channels():
    packet = create_simulated_telemetry(sample_index=4)
    assert packet.mode.value == "simulation"
    assert [sensor.name for sensor in packet.sensors] == [
        "ds18b20_1", "ds18b20_2", "ds18b20_3", "thermocouple_1", "thermocouple_2"
    ]
    assert all(sensor.valid and sensor.provenance.value == "simulated" for sensor in packet.sensors)


def test_api_ingest_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", TelemetryStore(tmp_path / "api.sqlite3"))
    client = TestClient(main.app)
    payload = sample_payload().model_dump(mode="json")
    response = client.post("/api/v1/telemetry", json=payload)
    assert response.status_code == 201
    record_id = response.json()["id"]
    duplicate = client.post("/api/telemetry", json=payload)
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == record_id
    history = client.get("/api/v1/telemetry?limit=10")
    assert history.status_code == 200
    assert history.json()[0]["id"] == record_id
    assert client.get("/api/telemetry/history?limit=10").json()[0]["id"] == record_id
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/health").json()["actuator_control"] == "disabled"
    assert client.get("/api/v1/telemetry/history?limit=10").json()[0]["id"] == record_id


def test_read_only_status_endpoints_require_measured_monitoring_data(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", TelemetryStore(tmp_path / "status.sqlite3"))
    client = TestClient(main.app)
    assert client.get("/api/device/status").json()["state"] == "OFFLINE"
    simulation = sample_payload().model_dump(mode="json")
    client.post("/api/v1/telemetry", json=simulation)
    assert client.get("/api/device/status").json()["state"] == "OFFLINE"

    measured = {
        "device_id": "dtc-itol-esp32-01", "mode": "monitoring",
        "controller_mode": "MONITOR_ONLY", "wifi_connected": True,
        "wifi_rssi_dbm": -38, "ip_address": "192.168.1.10",
        "sensors": [
            {"name": "thermocouple_primary", "value": 24.0, "valid": True, "provenance": "measured"},
            {"name": "primary_mk8", "kind": "heater_state", "value": 24.0,
             "valid": True, "provenance": "measured", "state": "DISABLED",
             "intended_state": "OFF", "control_mode": "MONITOR_ONLY"},
        ],
    }
    response = client.post("/api/v1/telemetry", json=measured)
    assert response.status_code == 201
    status = client.get("/api/device/status").json()
    assert status["state"] == "CONNECTED"
    assert status["controller_mode"] == "MONITOR_ONLY"
    assert status["wifi_rssi_dbm"] == -38
    sensor_status = client.get("/api/sensors/status").json()
    assert sensor_status["sensors"][0]["status"] == "VALID"
    actuator_status = client.get("/api/actuators/status").json()
    assert actuator_status["heaters"][0]["reported_state"] == "DISABLED"
    assert actuator_status["heaters"][0]["physical_state_verified"] is False
    assert actuator_status["peltier"]["assigned"] is False
    assert client.get("/api/faults").json()["faults"] == []


def test_api_rejects_missing_sensor_value(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", TelemetryStore(tmp_path / "invalid.sqlite3"))
    payload = sample_payload().model_dump(mode="json")
    payload["sensors"][0]["value"] = None
    response = TestClient(main.app).post("/api/v1/telemetry", json=payload)
    assert response.status_code == 422
