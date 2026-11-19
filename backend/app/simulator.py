"""Deterministic, explicitly simulated telemetry publisher for integration checks."""

import argparse
import math
import time

import httpx

from .schemas import OperatingMode, Provenance, SensorReading, TelemetryIn


def create_simulated_telemetry(sample_index: int = 0, device_id: str = "dtc-itol-sim-01") -> TelemetryIn:
    """Create a synthetic packet matching the firmware's five sensor channels."""
    phase = sample_index * 0.12
    definitions = (
        ("ds18b20_1", 27.0, 0.7, 0.0),
        ("ds18b20_2", 29.0, 0.5, 0.8),
        ("ds18b20_3", 31.0, 0.4, 1.6),
        ("thermocouple_1", 33.0, 0.8, 2.4),
        ("thermocouple_2", 35.0, 0.6, 3.2),
    )
    sensors = [
        SensorReading(
            name=name,
            value=round(baseline + amplitude * math.sin(phase + offset), 2),
            unit="°C",
            valid=True,
            provenance=Provenance.simulated,
        )
        for name, baseline, amplitude, offset in definitions
    ]
    return TelemetryIn(
        device_id=device_id,
        mode=OperatingMode.simulation,
        sensors=sensors,
    )


def post_simulated_sample(api_base: str, sample_index: int = 0, device_id: str = "dtc-itol-sim-01") -> dict:
    telemetry = create_simulated_telemetry(sample_index, device_id)
    response = httpx.post(
        f"{api_base.rstrip('/')}/api/v1/telemetry",
        json=telemetry.model_dump(mode="json"),
        timeout=5.0,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--device-id", default="dtc-itol-sim-01")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()
    if args.count < 1 or args.interval < 0:
        parser.error("count must be positive and interval cannot be negative")
    for index in range(args.count):
        result = post_simulated_sample(args.api_base, index, args.device_id)
        print(f"Stored simulated telemetry id={result['id']} device={result['device_id']}")
        if index + 1 < args.count:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
