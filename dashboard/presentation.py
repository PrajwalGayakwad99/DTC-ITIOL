"""Telemetry-to-display transformations used by the Streamlit pages."""

import math

import pandas as pd

from dashboard.sensor_status import (
    EXPECTED_SENSOR_CHANNELS,
    SENSOR_FRESHNESS_SECONDS,
    VERIFIED_DS18B20_ROM,
    configured_sensor_warnings,
    history_quality_counts,
    sensor_display_name,
    sensor_identity,
    sensor_rom,
    sensor_status_counts,
    sensor_status_rows,
)


def sensor_provenance_label(sensor: dict) -> str:
    source = str(sensor.get("provenance") or "unknown").upper()
    state = "valid" if sensor.get("valid") and sensor.get("value") is not None else "invalid"
    return f"{source} · {state}"


def temperature_history_frame(records: list[dict]) -> pd.DataFrame:
    """Return only finite, explicitly valid temperature samples with physical identity."""
    points: list[dict] = []
    for record in records:
        timestamp = record.get("timestamp_dt") or record.get("timestamp")
        for sensor in record.get("sensors", []):
            if sensor.get("kind", "temperature") != "temperature":
                continue
            if sensor_identity(sensor) == "not_configured:primary_ds18b20":
                continue
            value = sensor.get("value")
            try:
                numeric = float(value) if value is not None else None
            except (TypeError, ValueError):
                numeric = None
            if sensor.get("valid") and numeric is not None and math.isfinite(numeric) and timestamp:
                identity = sensor_identity(sensor)
                points.append({
                    "time": timestamp,
                    "sensor": sensor_display_name(sensor),
                    "identity": identity or "unmapped",
                    "temperature": numeric,
                    "provenance": sensor.get("provenance", "unknown"),
                    "sensor_rom": sensor_rom(sensor),
                })
    columns = ["time", "sensor", "identity", "temperature", "provenance", "sensor_rom"]
    frame = pd.DataFrame(points, columns=columns)
    if not frame.empty:
        frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["time"])
        frame["trace"] = frame["sensor"] + " · " + frame["provenance"].astype(str)
    return frame
