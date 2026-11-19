"""Shared, cached access to the existing FastAPI endpoints."""

import math
from datetime import datetime, timezone

import streamlit as st

from dashboard import api_client


def normalize_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def normalize_sensor(sensor: dict) -> dict:
    result = dict(sensor)
    raw_value = result.get("value")
    try:
        numeric_value = float(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        numeric_value = None
    if numeric_value is not None and not math.isfinite(numeric_value):
        numeric_value = None
    result["value"] = numeric_value
    result["valid"] = bool(result.get("valid")) and numeric_value is not None
    result["provenance"] = str(result.get("provenance") or "unknown").lower()
    result["error"] = result.get("error") or (None if result["valid"] else "missing or invalid reading")
    result["unit"] = result.get("unit") or "°C"
    result["name"] = str(result.get("name") or "unnamed_sensor")
    return result


def normalize_records(records: object) -> list[dict]:
    if not isinstance(records, list):
        return []
    normalized = []
    for record in records:
        if not isinstance(record, dict):
            continue
        row = dict(record)
        row["timestamp_dt"] = normalize_timestamp(row.get("timestamp"))
        row["sensors"] = [normalize_sensor(sensor) for sensor in row.get("sensors", []) if isinstance(sensor, dict)]
        normalized.append(row)
    return normalized


def latest_hardware_record(records: list[dict]) -> dict | None:
    """Select measured monitoring telemetry so newer simulations cannot pose as live hardware."""
    return next(
        (
            record for record in records
            if str(record.get("mode", "")).lower() == "monitoring"
            and any(
                sensor.get("kind", "temperature") == "temperature"
                and str(sensor.get("provenance", "")).lower() == "measured"
                for sensor in record.get("sensors", [])
            )
        ),
        None,
    )


def measured_monitoring_records(records: list[dict]) -> list[dict]:
    """Copy records while retaining only measured temperature samples in monitoring mode."""
    result = []
    for record in records:
        if str(record.get("mode", "")).lower() != "monitoring":
            continue
        row = dict(record)
        row["sensors"] = [
            sensor for sensor in record.get("sensors", [])
            if sensor.get("kind", "temperature") == "temperature"
            and str(sensor.get("provenance", "")).lower() == "measured"
        ]
        result.append(row)
    return result


@st.cache_data(ttl=1, show_spinner=False)
def load_snapshot(api_base: str, device_id: str, limit: int = 1000) -> dict:
    params = {"limit": limit}
    if device_id:
        params["device_id"] = device_id
    raw_records, telemetry_error = api_client.fetch_json(api_base, "/api/v1/telemetry", params)
    health, health_error = api_client.fetch_json(api_base, "/health")
    device_status, device_status_error = api_client.fetch_json(api_base, "/api/device/status", params={"device_id": device_id} if device_id else None)
    records = normalize_records(raw_records) if telemetry_error is None else []
    latest_hardware = latest_hardware_record(records)
    return {
        "records": records,
        # Only measured monitoring packets may populate live sensor cards.
        # Other packets remain available as history metadata for explicit source reporting.
        "latest": latest_hardware,
        "latest_packet": records[0] if records else None,
        "latest_hardware": latest_hardware,
        "telemetry_error": telemetry_error,
        "health": health if isinstance(health, dict) else None,
        "health_error": health_error,
        "device_status": device_status if isinstance(device_status, dict) else None,
        "device_status_error": device_status_error,
        "fetched_at": datetime.now(timezone.utc),
    }


def freshness(latest: dict | None, now: datetime | None = None) -> tuple[str, str]:
    """Infer device contact from packet freshness; the API has no device ping."""
    if not latest:
        return "No telemetry", "No ESP32 packet has reached the backend."
    has_measured_channel = any(
        str(sensor.get("provenance", "")).lower() == "measured"
        for sensor in latest.get("sensors", [])
    )
    if str(latest.get("mode", "")).lower() != "monitoring" or not has_measured_channel:
        return "No hardware data", "No measured monitoring packet is available to confirm live physical sensor telemetry."
    timestamp = latest.get("timestamp_dt")
    if timestamp is None:
        return "Unknown", "Latest packet has no valid timestamp."
    current = now or datetime.now(timezone.utc)
    age_seconds = max(0.0, (current - timestamp).total_seconds())
    if age_seconds <= 15:
        return "Connected", f"Latest packet timestamp is {age_seconds:.0f}s old."
    if age_seconds <= 60:
        return "Stale", f"Latest packet timestamp is {age_seconds:.0f}s old."
    return "Offline", f"Latest measured packet is {age_seconds / 60:.1f} min old; no recent ESP32 packet is available."
