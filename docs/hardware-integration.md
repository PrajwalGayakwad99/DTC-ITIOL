# DTC-ITOL hardware telemetry integration

## Current communication path

```text
DS18B20 / MAX6675 → ESP32 local acquisition and safety checks
                  → HTTP POST /api/v1/telemetry
                  → FastAPI validation and SQLite persistence
                  → Streamlit read-only live monitor
```

The ESP32 remains the only local controller. The backend never writes GPIO. The dashboard has no actuator command controls. The firmware's default remains `MONITOR_ONLY`, heater GPIO25/26/27 start as inputs, and this commissioning build denies `PHYSICAL_CONTROL`.

The dashboard refreshes at three-second intervals. It reads measured telemetry from FastAPI and does not use the software simulator as an operational fallback. Simulation packets remain usable as test fixtures, but are excluded from the dashboard's live sensor cards, measured history, and online-device classification.

## Telemetry contract

The existing `POST /api/v1/telemetry` endpoint and `sensors[]` data contract remain supported. Firmware telemetry includes:

- `device_id`, `mode: "monitoring"`, and the latest `timestamp` assigned by FastAPI when the device omits one.
- `controller_mode`: `MONITOR_ONLY`, `DRY_RUN`, or `PHYSICAL_CONTROL`.
- `wifi_connected`, `wifi_rssi_dbm`, and `ip_address` when Wi-Fi is connected.
- Measured sensor records with `value`, `valid`, `provenance`, optional `sensor_rom`, and `error`.
- Heater records with `kind: "heater_state"`, `state`, `intended_state`, `control_mode`, target, sensor validity, and any fault/inhibit.

Invalid sensor values remain JSON `null`. A valid 0°C MAX6675 reading is not changed to missing. DS18B20 labels are accepted as verified physical roles only when the full expected ROM is present. Heater state is reported from firmware state; no feedback circuit exists to independently verify MOSFET or load behavior.

The SQLite telemetry table gains nullable device metadata columns through an additive startup migration. Existing rows and the JSON sensor payload remain intact. Older firmware packets without these fields continue to parse and are shown with unavailable network metadata.

## HTTP endpoints

| Method and path | Purpose |
|---|---|
| `GET /health` | FastAPI health and `actuator_control: disabled`. |
| `GET /api/health` | Alias of the existing health response. |
| `POST /api/v1/telemetry` | Existing ESP32 ingestion route; returns HTTP 201 after SQLite storage. |
| `POST /api/telemetry` | Backward-compatible unversioned alias for ingestion. |
| `GET /api/v1/telemetry?limit=100&device_id=...` | Existing descending telemetry history route. |
| `GET /api/v1/telemetry/history?limit=100&device_id=...` | Backward-compatible explicit history alias. |
| `GET /api/telemetry/history?limit=100&device_id=...` | Unversioned history alias. |
| `GET /api/device/status?device_id=...` | Latest measured monitoring packet, online/stale/offline inference, controller mode, Wi-Fi RSSI and IP. |
| `GET /api/sensors/status?device_id=...` | Sensor readings, validity, stale state, provenance, ROM, and error from latest measured packet. |
| `GET /api/actuators/status?device_id=...` | Firmware-reported heater states and requested states, explicitly marked not physically verified; Peltier unassigned/disabled. |
| `GET /api/faults?device_id=...` | Faults present in the latest measured packet. This is not a historical fault log. |

Device state is inferred from packet age: connected up to 15 seconds, stale up to 60 seconds, then offline. No ESP32 ping is implemented. A simulation-only packet does not make the hardware device appear online.

## Network configuration

1. Copy `firmware/dtc_itol/secrets.example.h` to `firmware/dtc_itol/secrets.h` and set the Wi-Fi SSID/password locally. Keep the private header out of version control.
2. Set `DTC_ITOL_API_URL` in the private header to `http://<laptop-LAN-IP>:8000/api/v1/telemetry`. The firmware fallback preserves the existing endpoint, but that address may not match the current laptop network.
3. Start FastAPI on the local network interface: `python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`.
4. In Windows Firewall, allow port 8000 only on the trusted private network profile. Do not expose the service to a public network; this prototype does not authenticate device requests.
5. Start Streamlit with optional settings: `$env:DTC_ITOL_API_BASE="http://127.0.0.1:8000"` and `$env:DTC_ITOL_DEVICE_ID="dtc-itol-esp32-01"`; then run `python -m streamlit run dashboard/app.py`.
6. Confirm the latest device packet timestamp advances and the reported firmware mode is `MONITOR_ONLY`. If the API is unavailable, the dashboard displays an error and does not substitute generated temperatures.

## Control and safety boundary

No `POST /api/device/command` route is provided. The current firmware does not implement an authenticated network command receiver, and adding a backend endpoint alone would not safely control it. The dashboard stays read-only. The local Serial `p` request remains locked by `validSafetyConfiguration()`.

GPIO27 is assigned to secondary heater 2; the Peltier has no independent GPIO assignment. Peltier control is not implemented. Do not energize actuators from this integration. The private safety configuration remains fail-closed because driver topology, driver logic compatibility, active polarity, independent hardware over-temperature protection, and reviewed per-heater maximum limits are not established in source-controlled evidence. Software checks do not replace independent hardware protection.

Two control-specification conflicts remain unresolved: the integration request specifies a 100°C primary target while the established firmware target is 150°C; it also requests synchronized secondary heaters while the existing firmware computes each secondary channel independently. Existing firmware targets and independent behavior are preserved. Resolve these points in a reviewed control specification before any future physical-control implementation.

## Commissioning evidence still required

- Arduino compilation with the actual ESP32 board package and installed sensor libraries, then a deliberate user-controlled USB upload.
- Serial evidence that each exact DS18B20 ROM and both MAX6675 channels produce valid measured readings; exercise safe disconnected-sensor diagnostics with load supplies isolated.
- A captured device packet matched to the FastAPI response, SQLite record, and dashboard values; verify the reported IP/RSSI and three-second cadence.
- Point-to-point GPIO wiring inspection, 3.3 V logic compatibility, MOSFET inactive/active electrical behavior, and independently verified emergency/over-temperature protection.
- Reviewed maximum temperature limits, as-built power distribution and load nameplates, plus a separately approved commissioning procedure before any later physical-control work.

No physical sensor, network connection to a real ESP32, MOSFET switching, heater operation, Peltier operation, or electrical protection test is claimed by software validation.
