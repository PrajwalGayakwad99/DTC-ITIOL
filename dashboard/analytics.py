"""Calculated indicators for valid dashboard telemetry."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


RANGE_DELTAS = {
    "1 hour": timedelta(hours=1),
    "6 hours": timedelta(hours=6),
    "24 hours": timedelta(hours=24),
    "7 days": timedelta(days=7),
    "All available": None,
}


def filter_time_range(frame: pd.DataFrame, label: str, now: datetime | None = None) -> pd.DataFrame:
    if frame.empty or label == "All available" or label not in RANGE_DELTAS:
        return frame.copy()
    delta = RANGE_DELTAS[label]
    if delta is None:
        return frame.copy()
    current = now or datetime.now(timezone.utc)
    cutoff = pd.Timestamp(current - delta)
    return frame[frame["time"] >= cutoff].copy()


def summarize_sensor(frame: pd.DataFrame, sensor_name: str) -> dict | None:
    values = frame.loc[frame["sensor"] == sensor_name, "temperature"].dropna()
    if values.empty:
        return None
    return {
        "count": int(values.count()),
        "minimum": float(values.min()),
        "maximum": float(values.max()),
        "average": float(values.mean()),
        "standard_deviation": float(values.std(ddof=0)),
    }


def trend_slope_c_per_hour(frame: pd.DataFrame, sensor_name: str) -> float | None:
    subset = frame.loc[frame["sensor"] == sensor_name, ["time", "temperature"]].dropna().sort_values("time")
    if len(subset) < 2:
        return None
    elapsed = (subset["time"] - subset["time"].iloc[0]).dt.total_seconds().to_numpy() / 3600.0
    if np.unique(elapsed).size < 2:
        return None
    slope = float(np.polyfit(elapsed, subset["temperature"].to_numpy(dtype=float), 1)[0])
    return slope if np.isfinite(slope) else None


def experiment_telemetry(records: list[dict], experiment: dict) -> list[dict]:
    start = experiment.get("start_at")
    end = experiment.get("end_at")
    try:
        start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00")) if start else None
        end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00")) if end else None
    except ValueError:
        return []
    if start_dt is None or end_dt is None or start_dt.tzinfo is None or end_dt.tzinfo is None:
        return []
    start_dt = start_dt.astimezone(timezone.utc)
    end_dt = end_dt.astimezone(timezone.utc)
    return [
        record for record in records
        if record.get("timestamp_dt") is not None and start_dt <= record["timestamp_dt"] <= end_dt
    ]
