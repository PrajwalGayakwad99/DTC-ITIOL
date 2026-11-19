# Wireless sensor monitoring and USB commissioning

## Current state and power caution

The sketch is configured for physical sensor monitoring (`SIMULATION_MODE 0`) and wireless telemetry every three seconds. OTA is deliberately disabled in this USB commissioning build; no OTA password is required for normal USB operation. Heater operation defaults to `MONITOR_ONLY`; GPIO25/26/27 start as `INPUT` and stay high impedance until a local explicit mode request and all firmware safety gates pass. `DRY_RUN` reports intended states only. Peltier, pump, and fan control are not implemented; GPIO27 is now secondary heater 2.

The current temporary power arrangement is laptop USB directly to the ESP32. The XL4015 is excluded from ESP32 power because it has not been repaired and its output has not been verified. Do not reconnect it until repaired and its output has been measured with the ESP32 disconnected. Never connect 12 V directly to the ESP32.

Power the DS18B20 sensors from ESP32 3.3 V and join sensor grounds to ESP32 GND. Verify MAX6675 module supply compatibility before connecting. Keep the two 12 V / 10 A SMPS units on their separate documented 12 V load distribution; do not parallel their outputs. Software monitoring is not independent over-temperature protection.

## GPIO wiring reference

| Signal | ESP32 DevKit V1 GPIO |
| --- | ---: |
| Three DS18B20 DATA wires, shared OneWire bus | 4 |
| MAX6675 primary SCK | 18 |
| MAX6675 primary SO | 19 |
| MAX6675 primary CS | 22 |
| MAX6675 secondary CS | 21 |
| Primary MK8 heater, MOSFET 1 | 25 |
| Secondary heater 1, MOSFET 2 | 26 |
| Secondary heater 2, MOSFET 3 | 27 |
| Peltier control | Unassigned and disabled |

The two MAX6675 modules share SCK and SO and use separate chip-select pins. Firmware labels the thermocouples primary and secondary; physical placement must be checked by the user. The primary high-temperature location must use its MAX6675 channel. A DS18B20 is specified only up to 125 °C and must never be used to validate a 150 °C target. Confirm the secondary location is appropriate for the 100 °C target and the DS18B20 limit.

## DS18B20 commissioning and mapping

The local `sensor_addresses.h` is excluded from Git. Three ROM-to-location assignments have been provided and are read directly by ROM address; startup discovery is diagnostic only and does not assign physical locations by enumeration order. The firmware checks the ROM CRC and sends missing or invalid values as `null`. The primary DS18B20 role remains unassigned because the primary heater uses MAX6675 channel 1.

The existing local mapping is:

- Secondary heater 2: `DTC_ITOL_DS18B20_SECONDARY2_ROM` = `28 20 6B 79 00 00 00 F2`
- Coolant: `DTC_ITOL_DS18B20_COOLANT_ROM` = `28 74 1C 78 00 00 00 B3`
- Secondary heater 1 DS18B20 monitoring channel: `DTC_ITOL_DS18B20_SECONDARY_ROM` = `28 8B 1F 78 00 00 00 AB`
- Primary heater: `DTC_ITOL_DS18B20_PRIMARY_ROM` remains all-zero/unassigned; heater temperature uses MAX6675 channel 1.

Secondary heater 1's heater-control sensor remains MAX6675 channel 2, as in the existing firmware. The DS18B20 ending `AB` remains a separately named secondary-heater-1 temperature channel and does not silently replace the control sensor. The DS18B20 ending `F2` supplies secondary heater 2 temperature and its local safety check. The `sensor_rom` telemetry metadata lets the dashboard verify DS18B20 role labels against the configured address; a missing or mismatched address is shown as unverified. If a mapped device later disconnects, its reading is invalid and telemetry value is null. The MAX6675 library reports an open thermocouple as `NAN`; this is sent as invalid/null. A valid 0 °C thermocouple value remains valid.

## Heater operating modes and safety gate

- Boot and restart always select `MONITOR_ONLY`; the three heater GPIOs initialize as `INPUT`. No dashboard or network command changes operating mode.
- Type `m` in the local Serial Monitor for `MONITOR_ONLY`, `d` for `DRY_RUN`, `p` to request `PHYSICAL_CONTROL`, `r` to request reset of resolved latched faults, and `?` for help. A physical-mode request is rejected unless configuration gates pass, every channel is fresh/valid, the secondary heater 2 ROM is mapped, all maximum limits are configured above target, and no fault is latched.
- `heater_safety_config.h` is private and ignored by Git. Its current flags are all zero, output levels are unset, max limits are zero, and therefore physical mode is currently impossible. The example header contains only these blocking placeholders. Do not edit any flag until the driver circuit topology, input logic compatibility, inactive/active polarity, and independent over-temperature protection have actually been verified. Select max limits from an approved safety plan; they are not guessed here.
- Target temperatures are primary 150°C, secondary 1 100°C, and secondary 2 100°C. Hysteresis (default 2°C), minimum requested transition interval (default 10 seconds), and stale threshold (default 9 seconds) are set in the private `heater_safety_config.h`. Sensor invalidity, stale data, and over-temperature latch a per-channel fault; reset is an explicit local `r` command and is refused until readings are fresh and below the configured safe reset threshold.
- MONITOR_ONLY and DRY_RUN never request physical output writes. In PHYSICAL_CONTROL the sensor checks run locally and do not depend on Wi-Fi or FastAPI. HTTP requests are bounded to 250 ms so network failures do not stall the loop for the former multi-second timeout.
- This software is not independent hardware over-temperature protection. Do not energize heaters until independent protection and the MOSFET/driver wiring and logic have been validated. Firmware tests are not hardware validation.

## Private configuration

Copy `secrets.example.h` to `secrets.h` and set the Wi-Fi SSID/password. `secrets.h` is ignored by Git. An OTA password is not needed for USB uploads or Wi-Fi telemetry because OTA is disabled in this build.

Set `DTC_ITOL_API_URL` in the private `secrets.h` to the laptop's current IPv4 address ending in `/api/v1/telemetry`. The fallback address in the source preserves the existing endpoint but may not match the current network. The ESP32 and laptop must be on the same reachable private network.

## Laptop backend and dashboard

From the project root, start FastAPI so other devices on the trusted LAN can connect:

```powershell
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

Find the laptop IPv4 address with `ipconfig` and use the active Wi-Fi/Ethernet adapter address in `API_URL`. Allow inbound TCP port 8000 through Windows Firewall only on the trusted Private network profile. Test from the laptop at `http://127.0.0.1:8000/health`; from another device use `http://<laptop-ip>:8000/health`. The dashboard should use that same API host and will show connection errors if FastAPI is unreachable. Launch Streamlit in another terminal with:

```powershell
python -m streamlit run dashboard\app.py
```

The dashboard is a read-only live telemetry monitor, refreshes every three seconds, and displays measured sensor values only in its live cards and history. Configure its FastAPI address with `DTC_ITOL_API_BASE` and optional device filtering with `DTC_ITOL_DEVICE_ID`. It distinguishes measured telemetry from simulation-only packets. This firmware does not measure current, flow, leaks, or power.

## Arduino IDE: libraries and initial USB installation

1. Install the Espressif **esp32** board package and select **ESP32 Dev Module** for ESP32 DevKit V1.
2. Install **OneWire**, **DallasTemperature**, and the Adafruit **MAX6675 library** through Library Manager. `WiFi`, `HTTPClient`, and `ArduinoOTA` are provided by the ESP32 Arduino core.
3. Place `dtc_itol.ino`, `secrets.h`, `sensor_addresses.h`, and `heater_safety_config.h` together in the Arduino sketch folder (Arduino IDE may require a folder named `dtc_itol`). Install no additional control hardware based on this software change.
4. Keep actuator wiring/power disabled. Connect the ESP32 over USB for the initial upload. Select its USB port and compile/upload manually. No firmware is uploaded by this project workflow.
5. Open Serial Monitor at 115200 baud and follow the ROM discovery and network status messages.

## OTA status

OTA is disabled for this commissioning build. Use the ESP32 USB connection for firmware uploads. Serial Monitor reports this once during startup; Wi-Fi telemetry continues independently.

## Expected Serial Monitor output

```text
DS18B20 discovery: library reports 3 device(s).
DS18B20 mapping ds18b20_secondary_heater: 288B1F78000000AB
DS18B20 mapping ds18b20_coolant: 28741C78000000B3
DS18B20 mapping ds18b20_secondary_heater_2: 28206B79000000F2
Wi-Fi connected: IP <laptop-LAN-network-address>, RSSI <dBm>.
OTA disabled for this USB commissioning build; no OTA password is needed.
ds18b20_secondary_heater: <temperature> °C [MEASURED]
ds18b20_coolant: <temperature> °C [MEASURED]
ds18b20_secondary_heater_2: <temperature> °C [MEASURED]
Operating mode: MONITOR_ONLY. Peltier control is unassigned and disabled.
primary_mk8: state=DISABLED intended=OFF target=150.0°C
```

Faults are logged as invalid and serialized with `value: null`. No actual values are assumed by the example above. When a thermocouple is open, a `[SENSOR FAULT]` diagnostic should appear. Telemetry also includes controller mode and Wi-Fi RSSI/IP when connected. Passwords and Wi-Fi credentials must not appear in serial output.

## Troubleshooting and hardware validation checklist

- **No Wi-Fi / no telemetry:** verify SSID/password locally, same-network connectivity, API URL, Uvicorn bind address, Windows Firewall, and `/health` from the laptop and another LAN device. Sensor readings continue locally during Wi-Fi loss.
- **No OTA port:** expected in this build; use USB for firmware uploads. OTA is intentionally disabled.
- **No DS18B20 ROMs:** verify bus GPIO4, sensor power/ground, bus continuity, and the required pull-up arrangement for the actual modules. No pull-up component is asserted as part of the declared inventory; confirm the assembled bus meets the DS18B20 interface requirements.
- **Thermocouple fault:** check the thermocouple and MAX6675 connections and correct CS (22 primary, 21 secondary). Do not interpret invalid/null as zero degrees.
- **Before physical thermal operation:** verify the MAX6675 placements, driver topology, MOSFET input logic and polarity, configured maximum limits, and independent over-temperature protection. Although ROM-to-location values are supplied, the driver and over-temperature gates and maximum limits remain unresolved; the physical-mode request must remain denied.
- **Power:** keep the ESP32 off the XL4015 until it is repaired and its 5 V output is measured and verified unloaded and under the intended load. Never apply 12 V to VIN/5V or 3V3.

## Verification limits

The existing telemetry endpoint and sensor JSON remain compatible. New nullable top-level Wi-Fi/controller metadata is stored through additive SQLite columns; older packets remain valid. Read-only status routes are documented in [hardware integration](hardware-integration.md). No physical sensor, Wi-Fi, network-firewall, power-converter, driver, independent-protection, or heater-control hardware test is claimed.
