# DTC-ITOL development and integration status

## Current objective

Use the existing ESP32 firmware, FastAPI service, SQLite telemetry store, and Streamlit app for measured sensor monitoring. Keep the dashboard read-only and keep actuator control locked until hardware safety evidence is complete.

## Existing project audit

- The ESP32 firmware already performs ROM-addressed DS18B20 and two MAX6675 acquisition, validates readings, emits null for invalid sensors, posts JSON to `/api/v1/telemetry`, and defaults to `MONITOR_ONLY`.
- GPIO25/26/27 start as inputs. The private safety configuration has driver-verification flags unset, maximum limits unset, and the current firmware's physical-control gate returns false.
- GPIO27 maps to secondary heater 2. The Peltier has no separate GPIO assignment and remains unassigned; no fan or pump control is implemented.
- FastAPI validates telemetry and stores the current sensor JSON in SQLite. The Streamlit entry point had been switched to a generated thermal-cycle view and did not read the backend; it is now restored to a measured-telemetry read-only monitor.

## Implemented integration stages

1. **Telemetry metadata.** Firmware packets now include controller mode and Wi-Fi connected/RSSI/IP fields. Pydantic accepts them as optional, so older firmware clients remain compatible.
2. **SQLite compatibility.** New nullable metadata columns are added by startup migration; older records remain readable. Repeated device/timestamp packets return the existing record. History remains sorted by timestamp, including delayed packets.
3. **Read-only API status.** Added device, sensor, actuator, and current-fault status routes plus `/api/v1/telemetry/history`. The original `/health`, `POST /api/v1/telemetry`, and `GET /api/v1/telemetry` routes remain.
4. **Dashboard integration.** The Streamlit page now calls FastAPI, refreshes every three seconds, supports API URL and device-ID configuration, shows five physically mapped temperature channels and telemetry provenance, displays online/stale/offline status, charts measured monitoring history only, and reports firmware heater states as unverified commands. Missing values display as N/A, never fabricated zeroes.
5. **Safety boundary.** No dashboard control panel or `/api/device/command` route was added. The current firmware has no authenticated network command receiver; the ESP32 remains the local authority and physical-control mode remains locked.

## Validation state

Software tests cover backend schema/persistence, additive SQLite migration, duplicate packet handling, read-only status routes, measured-vs-simulated source selection, sensor mapping labels, and firmware safety source contracts. Tests run under the project virtual environment with Python 3.14.7. Software tests do not prove physical sensor identity, Wi-Fi reachability, MOSFET behavior, thermal protection, or actuator switching.

## Remaining work before physical actuation

- Compile with the actual ESP32 Dev Module board package and installed OneWire, DallasTemperature, and MAX6675 libraries; then upload manually only after review.
- Verify exact DS18B20 ROM responses and both MAX6675 channels over USB Serial with all load supplies isolated.
- Configure current laptop LAN IP in private `secrets.h`; allow Uvicorn only on the trusted private network and verify a packet through FastAPI, SQLite, and the live dashboard.
- Record actual driver topology, GPIO inactive/active levels, 3.3 V logic compatibility, supply/load wiring and ratings, and independent over-temperature protection evidence.
- Establish and review per-heater maximum limits and safety hardware. Keep Peltier disabled until it has a separate verified GPIO and independent electrical configuration.

See [hardware integration](hardware-integration.md), [firmware setup](firmware-setup.md), and [hardware commissioning report](hardware-commissioning-report.md).
