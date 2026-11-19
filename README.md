# DTC-ITOL

Direct-to-Chip Two-Phase Immersion Thermal Orchestration Loop: an ESP32 temperature monitoring prototype with a safety-gated, independent three-heater control path. The firmware defaults to measured monitoring and wireless telemetry. OTA is disabled in the current USB commissioning build. Physical control is currently unavailable because the safety configuration is intentionally incomplete.

## Project status

- FastAPI receives validated sensor telemetry and stores it in SQLite.
- The current Streamlit entry point is a read-only live hardware monitor. It reads the existing FastAPI/SQLite history every three seconds, filters simulation and non-measured packets from live cards and charts, and shows sensor validity, device freshness, Wi-Fi metadata, and firmware-reported heater states.
- ESP32 firmware is set to real sensor monitoring (`SIMULATION_MODE 0`); it sends no simulated fallback values.
- Heater modes are `MONITOR_ONLY` (boot default), `DRY_RUN`, and an explicitly requested `PHYSICAL_CONTROL` path that remains hard-locked in this commissioning firmware. GPIO25/26/27 initialize as `INPUT`; do not enable output control until driver checks, independent over-temperature protection, and reviewed maximum limits are documented. Peltier/pump/fan control remains absent.
- Heater setpoints are 150°C for the primary MK8 and 100°C for each secondary channel. Primary and secondary heater 1 control temperatures remain MAX6675 channels 1 and 2. Verified DS18B20 mappings are secondary heater 2 (ROM ending F2), coolant (B3), and secondary heater 1 monitoring (AB); the primary DS18B20 slot remains unassigned.
- Sensor values carry `measured`, `estimated`, or `simulated` provenance. The Smart Power Management page uses verified nameplate inputs and user-entered scenario duties; unspecified load ratings remain unknown and no power is measured. No AI models are currently generated.
- Experiment description and time-window metadata are stored in the existing experiment notes field for backward compatibility. Telemetry association is local and timestamp-based because the API does not expose experiment IDs.
- There are no current, flow, leak, or power sensors, so the project does not claim those measurements.

See [the development plan](docs/development-plan.md) and [firmware setup guide](docs/firmware-setup.md).

## Current ESP32 and sensor power arrangement

- Power the ESP32 directly from the laptop USB port during sensor testing and telemetry monitoring.
- The XL4015 is excluded from ESP32 power while it cannot regulate correctly. Do not reconnect it until repaired and its output is measured and verified with the ESP32 disconnected.
- Never connect 12 V directly to the ESP32.
- DS18B20 sensors use the ESP32 3.3 V supply and share ESP32 ground. The MAX6675 modules also require their documented supply and common signal ground; verify module voltage compatibility before connecting.
- Keep the two 12 V / 10 A SMPS units on their separately documented load distribution. Do not parallel their outputs. Nameplate capacity is not a measured load or safety verification.
- Heater GPIO25/26/27 start INPUT/high impedance. The local safety config is fail-closed; do not enable physical control without completing its documented verification gates.
- Wi-Fi credentials are held in ignored local `firmware/dtc_itol/secrets.h`; no OTA password is required for USB firmware upload or Wi-Fi telemetry. OTA stays disabled in the current build. Copy `secrets.example.h` for a fresh checkout. Verified DS18B20 role ROMs are stored in ignored local `sensor_addresses.h`; the firmware checks CRC and reads them by ROM, not discovery order.

## Run the software

Python 3.13 is the project target. The current development checks were run with Python 3.14 because Python 3.13 was not installed in the environment.

From this project folder in PowerShell:

```powershell
python -m venv backend\.venv
.\backend\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python -m pip install -r dashboard\requirements.txt
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

For ESP32 access over your trusted local network, use the wireless startup command documented in [firmware setup](docs/firmware-setup.md): `python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`, and allow port 8000 only on the Windows Private network profile.

In another terminal, activate the same environment and launch the dashboard:

```powershell
.\backend\.venv\Scripts\Activate.ps1
python -m streamlit run dashboard\app.py
```

The dashboard opens at `http://localhost:8501`. It is read-only: there are no actuator controls or actuator API requests. The API health check is `http://127.0.0.1:8000/health`.

For an isolated software test feed without an ESP32, run:

```powershell
python -m backend.app.simulator --count 20 --interval 3
```

The simulator posts values marked `mode: simulation` and `provenance: simulated` to the local API for software tests only. The live dashboard excludes these packets from device status, live cards, and measured-history charts.

To receive Wi-Fi telemetry from an ESP32, copy `firmware/dtc_itol/secrets.example.h` to the ignored `firmware/dtc_itol/secrets.h`. Set Wi-Fi credentials there and set `DTC_ITOL_API_URL` to the laptop's current private LAN IPv4 address ending in `/api/v1/telemetry`. Start Uvicorn with `--host 0.0.0.0` and allow port 8000 only on the trusted private network. OTA is disabled in the current build.

The dashboard reads `DTC_ITOL_API_BASE` (default `http://127.0.0.1:8000`) and optional `DTC_ITOL_DEVICE_ID` environment variables. Set these in PowerShell before launch if needed:

```powershell
$env:DTC_ITOL_API_BASE = "http://127.0.0.1:8000"
$env:DTC_ITOL_DEVICE_ID = "dtc-itol-esp32-01"
python -m streamlit run dashboard\app.py
```

The device ID filter is also editable in the page. See [hardware integration and API details](docs/hardware-integration.md).

The active dashboard is a compact single-page monitor. Other prior analytics and experiment modules remain in the repository but are not exposed by this entry point. A historical system-event journal is not provided by the API.

SQLite files are created at `data/telemetry.sqlite3` and `data/experiments.sqlite3`. Override them with `DTC_ITOL_DB` and `DTC_ITOL_EXPERIMENT_DB` if needed.

## Run tests

From the project folder with the environment active:

```powershell
python -m pytest tests -q
```

## Safety boundaries

- The firmware is currently configured for physical sensor monitoring. Until ROM mapping is configured, DS18B20 readings are labeled by ROM and their physical locations must not be inferred. Invalid readings are sent as null.
- The dashboard remains read-only. Firmware heater state is local and is never commanded by Wi-Fi or the backend. Never rely on this software to make unverified driver circuits safe or to replace independent hardware over-temperature protection.
- This workflow has not uploaded firmware or energized loads; any firmware installation is a manual user action.
- Backend or Wi-Fi availability is not used in the local heater safety logic.
- No AI temperature trend analysis is included until real experimental data is collected.
