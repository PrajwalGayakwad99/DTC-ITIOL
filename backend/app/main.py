"""FastAPI telemetry receiver. This service has no actuator endpoints."""

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from .schemas import HealthResponse, TelemetryIn, TelemetryRecord
from .store import TelemetryStore


DEFAULT_DB = Path(__file__).resolve().parents[1] / ".." / "data" / "telemetry.sqlite3"
store = TelemetryStore(os.environ.get("DTC_ITOL_DB", str(DEFAULT_DB.resolve())))
app = FastAPI(title="DTC-ITOL Telemetry API", version="0.1.0")
DEVICE_STALE_AFTER_SECONDS = 15
DEVICE_OFFLINE_AFTER_SECONDS = 60


@app.get("/health", response_model=HealthResponse)
@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.post("/api/v1/telemetry", response_model=TelemetryRecord, status_code=201)
@app.post("/api/telemetry", response_model=TelemetryRecord, status_code=201)
def ingest(telemetry: TelemetryIn) -> TelemetryRecord:
    try:
        return store.add(telemetry)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="telemetry could not be stored") from exc


@app.get("/api/v1/telemetry", response_model=list[TelemetryRecord])
def history(
    limit: int = Query(default=100, ge=1, le=1000),
    device_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> list[TelemetryRecord]:
    return store.latest(limit=limit, device_id=device_id)


@app.get("/api/v1/telemetry/history", response_model=list[TelemetryRecord])
@app.get("/api/telemetry/history", response_model=list[TelemetryRecord])
def telemetry_history_alias(
    limit: int = Query(default=100, ge=1, le=1000),
    device_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> list[TelemetryRecord]:
    """Explicit history route; retains the original /api/v1/telemetry endpoint."""
    return store.latest(limit=limit, device_id=device_id)


def _device_snapshot(device_id: str | None = None) -> tuple[TelemetryRecord | None, float | None, str]:
    records = store.latest(limit=1000, device_id=device_id)
    record = next((item for item in records
                   if item.mode.value == "monitoring"
                   and any(sensor.kind == "temperature" and sensor.provenance.value == "measured"
                           for sensor in item.sensors)), None)
    if record is None:
        return None, None, "OFFLINE"
    age = max(0.0, (datetime.now(timezone.utc) - record.timestamp).total_seconds())
    state = "CONNECTED" if age <= DEVICE_STALE_AFTER_SECONDS else (
        "STALE" if age <= DEVICE_OFFLINE_AFTER_SECONDS else "OFFLINE"
    )
    return record, age, state


@app.get("/api/device/status")
def device_status(device_id: str | None = Query(default=None, min_length=1, max_length=64)) -> dict:
    record, age, state = _device_snapshot(device_id)
    if record is None:
        return {"state": "OFFLINE", "device_id": device_id, "last_telemetry": None,
                "telemetry_age_seconds": None, "controller_mode": None,
                "wifi_connected": None, "wifi_rssi_dbm": None, "ip_address": None,
                "data_source": "DEVICE_OFFLINE"}
    return {
        "state": state,
        "device_id": record.device_id,
        "last_telemetry": record.timestamp.isoformat(),
        "telemetry_age_seconds": round(age or 0.0, 2),
        "controller_mode": record.controller_mode.value if record.controller_mode else None,
        "wifi_connected": record.wifi_connected,
        "wifi_rssi_dbm": record.wifi_rssi_dbm,
        "ip_address": record.ip_address,
        "data_source": "MEASURED_TELEMETRY" if record.mode.value == "monitoring" else "SIMULATION",
    }


@app.get("/api/sensors/status")
def sensors_status(device_id: str | None = Query(default=None, min_length=1, max_length=64)) -> dict:
    record, age, device_state = _device_snapshot(device_id)
    if record is None:
        return {"device_state": "OFFLINE", "sensors": []}
    rows = []
    for sensor in record.sensors:
        if sensor.kind != "temperature":
            continue
        status = "NOT_CONFIGURED" if sensor.error == "location_mapping_unconfigured" else (
            "INVALID" if not sensor.valid or sensor.value is None else
            "STALE" if device_state in {"STALE", "OFFLINE"} else "VALID"
        )
        rows.append({
            "name": sensor.name, "value": sensor.value if sensor.valid else None,
            "unit": sensor.unit, "status": status, "valid": sensor.valid,
            "provenance": sensor.provenance.value, "sensor_rom": sensor.sensor_rom,
            "error": sensor.error, "age_seconds": round(age or 0.0, 2),
        })
    return {"device_id": record.device_id, "device_state": device_state,
            "last_telemetry": record.timestamp.isoformat(), "sensors": rows}


@app.get("/api/actuators/status")
def actuators_status(device_id: str | None = Query(default=None, min_length=1, max_length=64)) -> dict:
    record, _age, device_state = _device_snapshot(device_id)
    if record is None:
        return {"device_state": "OFFLINE", "controller_mode": None, "heaters": [],
                "peltier": {"state": "DISABLED", "assigned": False,
                            "physical_state_verified": False}}
    heaters = []
    for sensor in record.sensors:
        if sensor.kind == "heater_state":
            heaters.append({
                "name": sensor.name, "reported_state": sensor.state or "UNKNOWN",
                "requested_state": sensor.intended_state or "UNKNOWN",
                "control_mode": sensor.control_mode,
                "sensor_valid": sensor.valid, "temperature_c": sensor.value,
                "target_c": sensor.target_c, "fault": sensor.error,
                "physical_state_verified": False,
            })
    return {
        "device_id": record.device_id,
        "device_state": device_state,
        "controller_mode": record.controller_mode.value if record.controller_mode else None,
        "heaters": heaters,
        "peltier": {"state": "DISABLED", "assigned": False,
                    "physical_state_verified": False},
        "note": "Reported states are firmware telemetry; no actuator feedback hardware is available.",
    }


@app.get("/api/faults")
def current_faults(device_id: str | None = Query(default=None, min_length=1, max_length=64)) -> dict:
    record, _age, device_state = _device_snapshot(device_id)
    faults = []
    if record is not None:
        for sensor in record.sensors:
            if sensor.error or (sensor.kind == "heater_state" and sensor.state == "FAULT"):
                faults.append({"name": sensor.name, "kind": sensor.kind,
                               "state": sensor.state, "error": sensor.error,
                               "sensor_rom": sensor.sensor_rom})
    return {"device_state": device_state, "device_id": record.device_id if record else device_id,
            "last_telemetry": record.timestamp.isoformat() if record else None,
            "faults": faults,
            "scope": "Current faults in latest telemetry only; historical fault events are not stored."}
