"""Validated telemetry contracts shared by device and dashboard clients."""

from datetime import datetime, timezone
from enum import Enum
from ipaddress import IPv4Address

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OperatingMode(str, Enum):
    simulation = "simulation"
    monitoring = "monitoring"


class Provenance(str, Enum):
    measured = "measured"
    estimated = "estimated"
    simulated = "simulated"


class ControllerMode(str, Enum):
    monitor_only = "MONITOR_ONLY"
    dry_run = "DRY_RUN"
    physical_control = "PHYSICAL_CONTROL"


class SensorReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=48)
    value: float | None
    unit: str = Field(default="°C", min_length=1, max_length=12)
    valid: bool
    provenance: Provenance
    error: str | None = Field(default=None, max_length=120)
    sensor_rom: str | None = Field(default=None, max_length=16)
    # Backward-compatible optional fields used by firmware heater-state records.
    kind: str = Field(default="temperature", max_length=24)
    target_c: float | None = None
    state: str | None = Field(default=None, max_length=16)
    intended_state: str | None = Field(default=None, max_length=16)
    control_mode: str | None = Field(default=None, max_length=24)
    progress_pct: float | None = Field(default=None, ge=0.0, le=100.0)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float | None) -> float | None:
        if value is not None and not (-100.0 <= value <= 1500.0):
            raise ValueError("temperature is outside the supported sensor range")
        return value

    @field_validator("valid")
    @classmethod
    def valid_requires_value(cls, valid: bool, info):
        value = info.data.get("value")
        if valid and value is None:
            raise ValueError("a valid reading must include a value")
        return valid

    @field_validator("sensor_rom")
    @classmethod
    def sensor_rom_must_be_hex(cls, value: str | None) -> str | None:
        if value is not None and (len(value) != 16 or any(char not in "0123456789abcdefABCDEF" for char in value)):
            raise ValueError("sensor_rom must contain exactly 16 hexadecimal characters")
        return value.upper() if value is not None else None


class TelemetryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64)
    mode: OperatingMode
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    controller_mode: ControllerMode | None = None
    wifi_connected: bool | None = None
    wifi_rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    ip_address: str | None = Field(default=None, max_length=15)
    sensors: list[SensorReading] = Field(min_length=1, max_length=16)

    @field_validator("ip_address")
    @classmethod
    def valid_ipv4_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(IPv4Address(value))
        except ValueError as exc:
            raise ValueError("ip_address must be a valid IPv4 address") from exc

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class TelemetryRecord(TelemetryIn):
    id: int


class HealthResponse(BaseModel):
    status: str = "ok"
    actuator_control: str = "disabled"
