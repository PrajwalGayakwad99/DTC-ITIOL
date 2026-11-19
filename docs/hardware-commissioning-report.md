# DTC-ITOL Hardware Commissioning Audit

**Audit date:** 2026-11-16  
**Scope:** source, configuration, documentation, API/database contract, dashboard, and available software checks.  
**Hardware status:** the user reports that all listed hardware is physically connected. This audit does not independently verify any wiring, voltage, sensor response, driver behavior, or load operation.

## Audit summary

The source and documentation agree on the sensor and heater GPIO assignments. The three configured DS18B20 ROMs match the user-provided locations, and their stored Dallas CRC bytes calculate correctly. Firmware reads those roles by configured ROM, validates CRC and readings, and marks unavailable temperatures invalid/null. MAX6675 channel 1 remains the primary heater measurement; channel 2 remains the secondary heater 1 control measurement.

The firmware defaults to `MONITOR_ONLY`. GPIO25, GPIO26, and GPIO27 start as `INPUT`; driver verification flags are zero, output polarity values are unset, maximum-temperature values are zero, and the firmware denies `PHYSICAL_CONTROL`. No loads were enabled in this audit. A GPIO configured as high impedance does not prove that an unverified external driver input is electrically off.

**Commissioning result: NOT CLEARED FOR HEATER, PELTIER, OR PUMP ENERGIZATION.** Independent hardware over-temperature protection, driver electrical behavior, maximum limits, live sensor communication, and power/load measurements lack recorded evidence. Do not change the local safety configuration to bypass these blockers.

## GPIO-to-component mapping

| ESP32 GPIO | Firmware/documented assignment | Audit result |
|---:|---|---|
| 4 | Shared DS18B20 OneWire data bus | Matches firmware and wiring guide; live bus not tested here. |
| 18 | Shared MAX6675 SCK | Matches firmware and wiring guide. |
| 19 | Shared MAX6675 SO | Matches firmware and wiring guide. |
| 22 | MAX6675 channel 1 CS; primary heater thermocouple | Matches constructor and wiring guide. |
| 21 | MAX6675 channel 2 CS; secondary heater 1 control thermocouple | Matches constructor and wiring guide. |
| 25 | Primary MK8 heater command, MOSFET 1 | Matches firmware and documentation; boot mode sets pin `INPUT`. |
| 26 | Secondary heater 1 command, MOSFET 2 | Matches firmware and documentation; boot mode sets pin `INPUT`. |
| 27 | Secondary heater 2 command, MOSFET 3 | Matches firmware and documentation; boot mode sets pin `INPUT`. |
| — | Peltier control | No Peltier GPIO assignment or control code is present. GPIO27 is assigned to secondary heater 2. |
| — | R385 pump and fans | No GPIO control assignment or firmware control is present. |

No documented GPIO conflict was found. This is a source/document comparison, not a continuity or point-to-point wiring test.

### Sensor-to-function assignments

| Sensor/channel | Configured physical role | Firmware use |
|---|---|---|
| MAX6675 CS1 GPIO22 | Primary heater | `thermocouple_primary`; heater target 150°C. |
| MAX6675 CS2 GPIO21 | Secondary heater 1 | `thermocouple_secondary`; existing control source, target 100°C. |
| DS18B20 ROM `28206B79000000F2` | Secondary heater 2 | `ds18b20_secondary_heater_2`; heater target 100°C. |
| DS18B20 ROM `28741C78000000B3` | Coolant | `ds18b20_coolant`; monitoring only. |
| DS18B20 ROM `288B1F78000000AB` | Secondary heater 1 monitoring | `ds18b20_secondary_heater`; this does not replace MAX6675 channel 2 as the heater control source. |
| DS18B20 primary slot | Unassigned | Remains zero/unassigned; primary heater uses MAX6675 channel 1. The firmware emits this slot invalid rather than treating a discovered sensor as the primary. |

The stored last bytes F2, B3, and AB match the calculated Dallas CRC-8 over each ROM’s first seven bytes. Firmware repeats its CRC check at runtime. This confirms the supplied ROM byte strings are internally CRC-consistent; it does not confirm which connected physical sensor emitted them.

## Sensor validation report

| Check | Source behavior | Evidence status |
|---|---|---|
| DS18B20 role mapping | Fixed role names use configured ROM constants. Discovery order is used for diagnostics only, not role selection. | Source verified; physical ROM response not tested. |
| DS18B20 ROM CRC | `OneWire::crc8(address, 7) == address[7]`; zero or invalid-CRC mappings are rejected. | Source verified; supplied ROM CRCs independently recalculated successfully. |
| DS18B20 validity | Rejects `DEVICE_DISCONNECTED_C`, non-finite values, and values outside -55°C to 125°C. Invalid values serialize as JSON `null`. | Source verified; no live or disconnection test performed. |
| MAX6675 validity | Rejects non-finite values and values outside 0°C to 1024°C; errors serialize as `null`. Valid 0°C is not automatically rejected. | Source verified; no live thermocouple/open-circuit test performed. |
| Freshness | Heater safety checks reject sensor samples older than the configured 9,000 ms threshold. Acquisition runs nominally every 3 seconds. | Source/config verified; no runtime timing fault injection performed. |
| Fault handling | Invalid, stale, or over-limit heater measurements latch a per-heater fault in ESP32 Preferences; explicit local reset is required after safe readings return. | Source verified; runtime latch/reset behavior not exercised on hardware. |
| Heater sensor authorization | `PHYSICAL_CONTROL` requires all heater readings valid and fresh, configured maximums, valid safety flags, persistent storage, and no latched fault. | Source verified; currently fails closed because required settings remain unset. |

No temperature values, sensor accuracy, thermal placement, sensor disconnection behavior, or electrical protection behavior are certified by this audit.

## MOSFET drivers and output states

- The firmware does not assume high-side/low-side topology or active-high/active-low polarity.
- The private `heater_safety_config.h` has topology, logic compatibility, polarity, and independent over-temperature verification flags set to `0`.
- Active and inactive output levels are `-1` (unset); all three maximum-temperature values are `0.0°C` (unset).
- At boot, GPIO25/26/27 are configured as `INPUT`; the global mode is `MONITOR_ONLY`. `DRY_RUN` does not invoke the physical output writer.
- A local `p` request is denied unless the configuration and live sample gates pass. This audit did not issue mode commands.
- Since the external driver input circuit is unverified, high impedance alone is not proof the MOSFET module cannot activate. Keep the heater supply disabled until the driver’s de-energized behavior and safe logic levels have been verified and recorded.
- No Peltier, pump, or fan control was found. GPIO27 is not shared with the Peltier in the firmware or documented map.

The firmware is not an independent over-temperature cutoff. The current inventory and safety configuration contain no recorded, verified independent hardware over-temperature protection.

## Power distribution audit

The project records two 12V, 10A SMPS units: **120W nameplate capacity each**, 240W combined arithmetic nameplate capacity. That combined figure is not proof that outputs may be paralleled or that a particular wiring arrangement is safe.

The dashboard model currently assumes this planning allocation:

| Modeled supply | Loads assigned by the dashboard model | Default rating state |
|---|---|---|
| SMPS 1 | R385 pump, primary cartridge heater, Peltier | All three load ratings unknown. Peltier is a planning input only; firmware does not control it. |
| SMPS 2 | Secondary heater 1, secondary heater 2, CPU tower fan, 12V brushless fan | All four load ratings unknown. |

At untouched defaults, the software reports a **0W known subtotal**, **120W rated capacity per supply**, and marks all assigned loads unknown. This is not a measured zero load and does not mean either supply has headroom under real operation. Once users enter nameplate values, the page multiplies those values by scenario duties; it still does not measure current or power.

The current power page uses one shared scenario-duty value for both secondary heaters, so it does not model their independent heater commands. The load-to-SMPS split is a model assumption, not a verified wiring record. No heater, pump, fan, or Peltier nameplate watts, measured current, branch protection, supply output behavior, or actual load allocation was established here.

## Dashboard and telemetry audit

- The existing read-only Streamlit dashboard includes latest temperature cards, history plots, device filtering, provenance labels, heater state cards, validity/fault diagnostics, experiment logging, and CSV exports.
- DS18B20 physical labels require `sensor_rom` metadata to match the expected ROM; a missing or mismatched ROM is shown as an unverified mapping. MAX6675 labels follow fixed firmware channel names.
- Heater status telemetry distinguishes actual `state`, `intended_state`, and `control_mode`; temperature values retain `measured` provenance and calculated progress is labeled as calculated.
- Firmware continues to POST to `/api/v1/telemetry`. `sensor_rom` and heater status are optional additions within `sensors[]`; the FastAPI model accepts them and SQLite stores the sensor objects in its existing JSON column. No endpoint or table change is required.
- Backend and dashboard were inspected, but full application execution and API-to-SQLite round-trip tests could not run in the current environment because FastAPI, Pydantic, Streamlit, Pandas, and pytest are unavailable.

## Commissioning checklist and evidence record

Use one row per channel/component. Mark “verified” only after the named test is completed and evidence is saved. The audit status reflects code/document review only unless expressly stated.

| Item | Component connection documented | Firmware configuration verified | Sensor communication verified | Electrical behavior verified | Safety requirement verified | Hardware test evidence recorded |
|---|---|---|---|---|---|---|
| GPIO4 / DS18B20 bus and ROMs AB, B3, F2 | Yes in project docs; physical connection user-reported | ROM values and fixed role mapping verified in source | **Not tested** | **Not tested** | CRC/range/fault code present; independent protection not applicable to coolant, still verify heater sensor roles | **No evidence attached** |
| MAX6675 channel 1, CS22 | Yes in project docs; physical connection user-reported | Primary thermocouple source verified in source | **Not tested** | **Not tested** | Primary heater control still blocked by driver/maximum/independent protection gates | **No evidence attached** |
| MAX6675 channel 2, CS21 | Yes in project docs; physical connection user-reported | Secondary heater 1 control source verified in source | **Not tested** | **Not tested** | Secondary heater control still blocked by driver/maximum/independent protection gates | **No evidence attached** |
| GPIO25 / MOSFET 1 / primary heater | Yes in project docs; physical connection user-reported | GPIO and MONITOR_ONLY boot state verified in source | N/A | **Not tested** | **Not verified; control gate remains closed** | **No evidence attached** |
| GPIO26 / MOSFET 2 / secondary heater 1 | Yes in project docs; physical connection user-reported | GPIO and MONITOR_ONLY boot state verified in source | N/A | **Not tested** | **Not verified; control gate remains closed** | **No evidence attached** |
| GPIO27 / MOSFET 3 / secondary heater 2 | Yes in project docs; physical connection user-reported | GPIO and MONITOR_ONLY boot state verified in source; Peltier unassigned | N/A | **Not tested** | **Not verified; control gate remains closed** | **No evidence attached** |
| Two 12V/10A SMPS and load distribution | Power inventory and modeled allocation documented; physical connection user-reported | Not an ESP32 firmware setting | N/A | **Not tested** | Actual load split, output behavior, ratings, and protection not verified | **No evidence attached** |
| Independent over-temperature protection | No verified device or implementation recorded | Safety flag remains `0` | N/A | **Not tested** | **Not verified; required blocker** | **No evidence attached** |

Evidence to record when commissioning is carried out: dated wiring photographs or as-built schematic, ESP32 board/core and library versions, Serial Monitor ROM/CRC and sensor validity log, telemetry packet plus matching API/SQLite record, measured supply/logic readings with instrument and conditions, driver inactive/active truth table, independent cutoff test record, reviewed maximum limits and approver, and signed checklist. No such hardware evidence was included with this audit.

## Step-by-step physical commissioning procedure

This workflow starts with sensor and telemetry checks only. Do not energize heaters, the Peltier, or the pump as part of these checks. Do not send the Serial `p` command.

1. **Keep load energy isolated.** Leave both 12V SMPS outputs to heaters, Peltier, pump, and fans disabled while setting up firmware and telemetry. Power the ESP32 from laptop USB as documented. Do not use the unverified XL4015 to power the ESP32. Confirm no load can be energized by a floating driver input; if that cannot be confirmed, keep the load supply disconnected.
2. **Record the as-built wiring.** Photograph or draw the connected ESP32, sensor modules, driver inputs, grounds, and each SMPS branch. Compare every signal against the GPIO table above. Record any discrepancy; do not silently move pins.
3. **Review driver documentation and power path.** Before load power is connected, identify each MOSFET module’s topology, enable behavior, active polarity, allowable input voltage, and de-energized state from its actual marking/documentation and suitable electrical checks. Record observations. Do not change firmware polarity values based on assumption. Do not proceed if safe inactive behavior is unclear.
4. **Compile manually for ESP32 DevKit V1.** In Arduino IDE select the ESP32 Dev Module and install/confirm the project's OneWire, DallasTemperature, MAX6675, and ESP32 core dependencies. Compile without upload first if possible. Record versions and output. This environment had no Arduino toolchain, so no compile result is available from this audit.
5. **Start in `MONITOR_ONLY`.** With load supplies still disabled, use USB, open Serial Monitor at 115200 baud, reset the ESP32, and record that startup reports `MONITOR_ONLY`. Confirm the firmware never requests an actuator output in this mode. Do not enter `PHYSICAL_CONTROL`.
6. **Check DS18B20 discovery and exact ROMs.** Record each detected ROM; compare to the configured mapping: F2 secondary heater 2, B3 coolant, AB secondary heater 1. Confirm startup mapping lines report those configured addresses. Discovery order must not change the role labels. Confirm the primary DS18B20 slot remains unassigned.
7. **Check sensor validity without heating.** Verify each mapped DS18B20 produces a valid measured value and each MAX6675 channel returns a valid measured value at the existing sensor condition. Confirm MAX6675 channel 1 is the primary sensor and channel 2 is secondary heater 1's existing control source. A valid numeric response proves communication, not calibration or correct thermal placement. If accurate comparison is required, use an appropriately calibrated reference instrument; none is listed in the current inventory.
8. **Check faults only with loads disabled.** If a qualified operator elects to test disconnection behavior, first ensure load supplies remain off and safely de-energize the sensor circuit before changing connections. Verify a missing/invalid reading becomes `valid:false`, `value:null`, and an explanatory fault; restore the exact sensor mapping and verify the fault remains latched until conditions are safe and an explicit local reset is issued. Do not hot-plug sensors or test over-temperature by heating a load.
9. **Verify the FastAPI/SQLite path.** Start FastAPI and the dashboard using the existing project instructions. Confirm ESP32 packets arrive at `/api/v1/telemetry`, preserve `device_id`, timestamp, `mode`, validity, `provenance`, and `sensor_rom`, and appear in `/api/v1/telemetry` history and SQLite. Confirm the dashboard maps only matching ROMs and displays each channel’s validity/provenance and each heater’s state/mode/fault. Capture a packet and matching dashboard/API record as evidence.
10. **Check `DRY_RUN` only.** With all load supplies still off, enter `d` locally. Compare intended states with the measured temperatures and targets. Verify reported actual heater state remains `DISABLED` and GPIO25/26/27 remain inputs. Return to `m`. Do not use this step to authorize physical control.
11. **Verify stale/fault handling in software.** Run the automated tests in an environment with the declared dependencies installed. Confirm stale data, invalid readings, over-temperature logic, persistent fault, and explicit reset tests. The current suite contains firmware source-contract checks, not an ESP32 hardware-in-the-loop test. Record the exact test output.
12. **Close safety blockers before any later heater commissioning.** Obtain a reviewed independent hardware over-temperature protection design/test, verify each driver’s actual inactive/active levels and electrical compatibility, set reviewed maximum temperatures (secondary heater 2 limit must not exceed the DS18B20's 125°C rating), confirm physical sensor placement, and complete load ratings/power allocation checks. Have the responsible engineer review evidence and explicitly authorize a separate physical-control commissioning plan. Keep the present default mode and interlocks intact until then.

## Remaining blockers

1. No verified independent hardware over-temperature protection is documented.
2. MOSFET topology, driver inactive state, logic compatibility, and active polarity have not been electrically verified; output levels remain unset.
3. All three maximum-temperature limits are unset (`0.0°C`), so the firmware's physical-control gate cannot pass.
4. No live sensor readings, sensor accuracy/placement evidence, stale/fault injection record, or MAX6675 open-sensor test evidence is available from this audit.
5. Actual power/load allocation and nameplate ratings are not recorded; the dashboard calculations are estimates only and use a shared duty for both secondary heater scenarios.
6. No Arduino compiler is available in this environment, and the full Python test suite cannot run because pytest and application dependencies are missing.

## Software validation actually run

- Eight existing firmware source-contract test functions were invoked directly: **8 passed**. They inspect source text; they are not hardware behavior tests.
- Six dashboard ROM/channel display-label checks were run against the mapping helper: **6 passed**. Full Streamlit rendering was not run.
- `python -m compileall -q backend dashboard tests` under Python 3.14.7: **passed**.
- `python -m pytest tests -q`: **not run**; Python reports `No module named pytest`. FastAPI, Pydantic, Streamlit, and Pandas are also unavailable in this environment.
- Arduino compilation: **not run**; `arduino-cli`, `g++`, and `clang++` are unavailable.
- No firmware upload, output drive, electrical measurement, sensor-bus read, SMPS test, or load energization was performed by this audit.

## Integration addendum (2026-11-19)

The dashboard entry point now reads the FastAPI telemetry API instead of generating the displayed thermal cycle. It filters live cards and plots to measured monitoring telemetry, verifies DS18B20 roles against full ROMs, and reports device state from packet age. FastAPI now offers additive read-only device, sensor, actuator, fault, and history routes. Optional Wi-Fi/controller fields are persisted through a backward-compatible SQLite column migration. The existing POST endpoint and sensor JSON remain supported. The local `data/telemetry.sqlite3` schema was migrated additively; historical rows were retained.

Latest stored telemetry inspection found 1,292 monitoring packets and 231 simulation packets. The latest stored monitoring packet timestamp was **2026-11-17 19:26:58 UTC**, so it is stale as of this addendum and does not prove a current ESP32 connection. Several records use older DS18B20 names; the current dashboard will not treat those as verified mappings without matching ROM metadata.

Validation in `backend/.venv` with Python 3.14.7: `python -m compileall -q backend dashboard tests` completed, and `python -m pytest tests -v` reported **58 passed**. The Streamlit app rendered under `AppTest` with a mocked API packet. Arduino CLI/IDE and PlatformIO are not installed, so firmware compilation was not run. No physical sensor, Wi-Fi, MOSFET, heater, Peltier, pump, fan, or independent over-temperature test was run.

The attached control specification conflicts with existing settings: it asks for a 100°C primary target instead of the existing 150°C target and asks for synchronized secondary heaters instead of the existing independent channel calculations. These settings were not changed. `PHYSICAL_CONTROL` remains locked, heater GPIOs remain inputs in monitoring mode, and the Peltier remains unassigned. A reviewed resolution of those conflicts and the hardware commissioning blockers above is required before physical actuation.
