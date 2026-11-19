"""Physical channel identity and freshness classification for dashboard telemetry."""

from datetime import datetime, timezone
import math
import re


# Keep aligned with DTC_ITOL_SENSOR_STALE_MS in the private firmware config.
SENSOR_FRESHNESS_SECONDS = 9.0

VERIFIED_DS18B20_LABELS = {
    "288B1F78000000AB": "Secondary heater 1 monitoring · DS18B20 · ROM …00AB",
    "28206B79000000F2": "Secondary heater 2 monitoring · DS18B20 · ROM …00F2",
    "28741C78000000B3": "Coolant temperature · DS18B20 · ROM …00B3",
}
VERIFIED_DS18B20_ROM = {
    "ds18b20_secondary_heater": "288B1F78000000AB",
    "ds18b20_secondary_heater_2": "28206B79000000F2",
    "ds18b20_coolant": "28741C78000000B3",
}

EXPECTED_SENSOR_CHANNELS = (
    ("max6675:primary", "Primary heater · MAX6675 channel 1", "MAX6675 channel 1"),
    ("max6675:secondary", "Secondary heater 1 control · MAX6675 channel 2", "MAX6675 channel 2"),
    ("ds18b20:288B1F78000000AB", VERIFIED_DS18B20_LABELS["288B1F78000000AB"], "ROM …AB"),
    ("ds18b20:28206B79000000F2", VERIFIED_DS18B20_LABELS["28206B79000000F2"], "ROM …F2"),
    ("ds18b20:28741C78000000B3", VERIFIED_DS18B20_LABELS["28741C78000000B3"], "ROM …B3"),
)

UNCONFIGURED_PRIMARY_NAME = "ds18b20_primary_unassigned"
UNCONFIGURED_PRIMARY_LABEL = "Primary heater DS18B20 · NOT CONFIGURED"
_LEGACY_ROM_NAME = re.compile(r"^ds18b20_rom_([0-9a-f]{16})$", re.IGNORECASE)


def sensor_rom(sensor: dict) -> str | None:
    """Get address metadata, including legacy rows whose name carried the ROM."""
    raw = sensor.get("sensor_rom")
    if raw:
        candidate = str(raw).strip().upper()
        if len(candidate) == 16 and all(char in "0123456789ABCDEF" for char in candidate):
            return candidate
    match = _LEGACY_ROM_NAME.fullmatch(str(sensor.get("name", "")))
    return match.group(1).upper() if match else None


def sensor_identity(sensor: dict) -> str | None:
    """Return a stable identity independent of discovery order or telemetry name era."""
    if sensor.get("kind", "temperature") != "temperature":
        return None
    name = str(sensor.get("name", "unnamed_sensor")).lower()
    provenance = str(sensor.get("provenance", "unknown")).lower()
    if name == UNCONFIGURED_PRIMARY_NAME:
        return "not_configured:primary_ds18b20"

    address = sensor_rom(sensor)
    if address:
        return f"ds18b20:{address}"

    if name in {"thermocouple_primary", "thermocouple_1"}:
        return "simulated:thermocouple_1" if provenance == "simulated" and name == "thermocouple_1" else "max6675:primary"
    if name in {"thermocouple_secondary", "thermocouple_2"}:
        return "simulated:thermocouple_2" if provenance == "simulated" and name == "thermocouple_2" else "max6675:secondary"
    if provenance == "simulated":
        return f"simulated:{name}"
    return f"unmapped:{name}"


def sensor_display_name(sensor: dict) -> str:
    name = str(sensor.get("name", "unnamed_sensor")).lower()
    provenance = str(sensor.get("provenance", "unknown")).lower()
    if name == UNCONFIGURED_PRIMARY_NAME:
        return UNCONFIGURED_PRIMARY_LABEL

    address = sensor_rom(sensor)
    if address:
        physical_label = VERIFIED_DS18B20_LABELS.get(address)
        if physical_label:
            declared_roles = {
                "ds18b20_secondary_heater": "288B1F78000000AB",
                "ds18b20_coolant": "28741C78000000B3",
                "ds18b20_secondary_heater_2": "28206B79000000F2",
            }
            declared_address = declared_roles.get(name)
            if declared_address and declared_address != address:
                return f"{physical_label} · declared role mismatch"
            return physical_label
        return f"Unmapped DS18B20 · ROM …{address[-4:]}"

    if name in {"thermocouple_primary", "thermocouple_1"}:
        if provenance == "simulated":
            return "Simulated thermocouple channel 1"
        return "Primary heater · MAX6675 channel 1"
    if name in {"thermocouple_secondary", "thermocouple_2"}:
        if provenance == "simulated":
            return "Simulated thermocouple channel 2"
        return "Secondary heater 1 control · MAX6675 channel 2"
    if name.startswith("ds18b20_") and provenance == "simulated":
        return name
    if name.startswith("ds18b20_"):
        return f"Unverified DS18B20 mapping · {name}"
    return str(sensor.get("name", "unnamed_sensor"))


def _timestamp(record: dict) -> datetime | None:
    timestamp = record.get("timestamp_dt") or record.get("timestamp")
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            return None
        return timestamp.astimezone(timezone.utc)
    if not isinstance(timestamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _numeric_valid(sensor: dict) -> bool:
    if not sensor.get("valid") or sensor.get("value") is None:
        return False
    try:
        return math.isfinite(float(sensor["value"]))
    except (TypeError, ValueError):
        return False


def sensor_status_rows(
    records: list[dict],
    now: datetime | None = None,
    freshness_seconds: float = SENSOR_FRESHNESS_SECONDS,
) -> list[dict]:
    """Summarize five configured physical channels plus the unused primary DS18B20 role."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    samples: dict[str, list[tuple[datetime | None, dict]]] = {
        identity: [] for identity, _label, _source in EXPECTED_SENSOR_CHANNELS
    }
    primary_unassigned = False
    has_measured_context = False
    for record in records:
        for reading in record.get("sensors", []):
            identity = sensor_identity(reading)
            if (reading.get("kind", "temperature") == "temperature"
                    and str(reading.get("provenance", "")).lower() == "measured"
                    and str(record.get("mode", "monitoring")).lower() == "monitoring"):
                has_measured_context = True
            if identity == "not_configured:primary_ds18b20":
                primary_unassigned = True
            elif identity in samples and str(reading.get("provenance", "")).lower() == "measured":
                samples[identity].append((_timestamp(record), reading))

    rows = []
    for identity, label, source in EXPECTED_SENSOR_CHANNELS:
        candidates = samples[identity]
        candidates.sort(key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))
        sample_time, reading = candidates[-1] if candidates else (None, None)
        age = max(0.0, (current - sample_time).total_seconds()) if sample_time else None
        if reading is None:
            status = "INVALID" if has_measured_context else "SIMULATION_ONLY"
            error = "No measured sample for configured channel" if has_measured_context else "Simulation data does not verify physical sensor status"
            value = None
            provenance = None
        elif not _numeric_valid(reading):
            status, error = "INVALID", reading.get("error") or "Missing or invalid sensor reading"
            value = reading.get("value")
            provenance = reading.get("provenance")
        elif sample_time is None:
            status, error = "INVALID", "Reading timestamp unavailable"
            value = reading.get("value")
            provenance = reading.get("provenance")
        elif age > freshness_seconds:
            status, error = "STALE", f"Last valid sample is {age:.1f}s old"
            value = reading.get("value")
            provenance = reading.get("provenance")
        else:
            status, error = "VALID", ""
            value = reading.get("value")
            provenance = reading.get("provenance")
        rows.append({
            "identity": identity, "sensor": label, "source": source, "status": status,
            "value": value, "provenance": provenance, "age_seconds": age, "error": error,
            "configured": True,
        })

    if primary_unassigned or not records:
        rows.append({
            "identity": "not_configured:primary_ds18b20", "sensor": UNCONFIGURED_PRIMARY_LABEL,
            "source": "Primary heater uses MAX6675 channel 1", "status": "NOT_CONFIGURED",
            "value": None, "provenance": None, "age_seconds": None, "error": "",
            "configured": False,
        })
    return rows


def sensor_status_counts(rows: list[dict]) -> dict[str, int]:
    return {status: sum(row.get("status") == status for row in rows)
            for status in ("VALID", "INVALID", "STALE", "NOT_CONFIGURED", "SIMULATION_ONLY")}


def configured_sensor_warnings(rows: list[dict]) -> list[str]:
    return [f"{row['sensor']}: {row.get('error') or row['status']}"
            for row in rows if row.get("configured") and row.get("status") in {"INVALID", "STALE"}]


def history_quality_counts(records: list[dict]) -> dict[str, int]:
    counts = {"valid": 0, "invalid": 0, "null": 0, "non_finite": 0, "not_configured": 0}
    for record in records:
        for sensor in record.get("sensors", []):
            if sensor.get("kind", "temperature") != "temperature":
                continue
            if sensor_identity(sensor) == "not_configured:primary_ds18b20":
                counts["not_configured"] += 1
                continue
            value = sensor.get("value")
            if value is None:
                counts["null"] += 1
                counts["invalid"] += 1
            else:
                try:
                    finite = math.isfinite(float(value))
                except (TypeError, ValueError):
                    finite = False
                if not finite:
                    counts["non_finite"] += 1
                    counts["invalid"] += 1
                elif not sensor.get("valid"):
                    counts["invalid"] += 1
                else:
                    counts["valid"] += 1
    return counts
