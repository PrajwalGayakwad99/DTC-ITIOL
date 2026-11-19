from pathlib import Path
import re


PROJECT = Path(__file__).parents[1]
SKETCH = PROJECT / "firmware" / "dtc_itol" / "dtc_itol.ino"
SOURCE = SKETCH.read_text(encoding="utf-8")


def test_boot_is_monitor_only_and_actuator_pins_start_input():
    setup = SOURCE.split("void setup()", 1)[1].split("void loop()", 1)[0]
    assert "OperatingMode operatingMode = MONITOR_ONLY" in SOURCE
    assert "pinMode(PIN_PRIMARY_HEATER, INPUT)" in setup
    for pin in ("PIN_SECONDARY_HEATER_1", "PIN_SECONDARY_HEATER_2"):
        assert f"pinMode({pin}, INPUT)" in setup
    assert "PIN_PELTIER" not in SOURCE
    assert "SIMULATION_MODE 0" in SOURCE  # physical sensor monitoring, no simulated temperatures
    assert SOURCE.count(", NAN, false, false, 0, nullptr}") == 3


def test_heater_gpio_mapping_and_sensor_allocation_are_independent():
    for pin in (
        "PIN_DS18B20 = 4", "PIN_MAX6675_SCK = 18", "PIN_MAX6675_SO = 19",
        "PIN_MAX6675_CS1 = 22", "PIN_MAX6675_CS2 = 21",
        "PIN_PRIMARY_HEATER = 25", "PIN_SECONDARY_HEATER_1 = 26",
        "PIN_SECONDARY_HEATER_2 = 27",
    ):
        assert pin in SOURCE
    assert '"primary_mk8", PIN_PRIMARY_HEATER, 3, 150.0f' in SOURCE
    assert '"secondary_heater_1", PIN_SECONDARY_HEATER_1, 4, 100.0f' in SOURCE
    assert '"secondary_heater_2", PIN_SECONDARY_HEATER_2, 5, 100.0f' in SOURCE
    assert "thermocouplePrimary.readCelsius()" in SOURCE
    assert "thermocoupleSecondary.readCelsius()" in SOURCE
    assert "ds18b20.getTempC(*secondary2)" in SOURCE
    assert "DTC_ITOL_DS18B20_SECONDARY2_ROM" in (SKETCH.with_name("sensor_addresses.h").read_text())
    assert "{\"primary_mk8\", PIN_PRIMARY_HEATER, 3, 150.0f" in SOURCE
    assert "{\"secondary_heater_1\", PIN_SECONDARY_HEATER_1, 4, 100.0f" in SOURCE
    assert "readings[4] = {\"thermocouple_secondary\"" in SOURCE


def test_verified_ds18b20_roms_map_to_roles_without_discovery_order():
    config = SKETCH.with_name("sensor_addresses.h").read_text(encoding="utf-8").upper()
    assert "DTC_ITOL_DS18B20_SECONDARY2_ROM = {\n    0X28, 0X20, 0X6B, 0X79, 0X00, 0X00, 0X00, 0XF2" in config
    assert "DTC_ITOL_DS18B20_COOLANT_ROM = {\n    0X28, 0X74, 0X1C, 0X78, 0X00, 0X00, 0X00, 0XB3" in config
    assert "DTC_ITOL_DS18B20_SECONDARY_ROM = {\n    0X28, 0X8B, 0X1F, 0X78, 0X00, 0X00, 0X00, 0XAB" in config
    assert "DTC_ITOL_DS18B20_PRIMARY_ROM = {0, 0, 0, 0, 0, 0, 0, 0}" in config
    assert '"ds18b20_primary_unassigned",\n    "ds18b20_secondary_heater",\n    "ds18b20_coolant"' in SOURCE
    assert "&DTC_ITOL_DS18B20_PRIMARY_ROM,\n    &DTC_ITOL_DS18B20_SECONDARY_ROM,\n    &DTC_ITOL_DS18B20_COOLANT_ROM" in SOURCE
    assert "DTC_ITOL_DS18B20_SECONDARY2_ROM" in SOURCE
    read_body = SOURCE.split("void readDS18B20()", 1)[1].split("void recordMAX6675Reading", 1)[0]
    assert "DS18B20_ROLE_ADDRESSES[i]" in read_body
    assert "getTempC(*address)" in read_body
    assert "discoveredAddresses[i]" not in read_body
    assert "location_mapping_unconfigured" in read_body
    assert "OneWire::crc8(address, 7)" in SOURCE
    telemetry = SOURCE.split("String buildTelemetryJSON()", 1)[1].split("void sendTelemetry", 1)[0]
    assert "sensor_rom" in telemetry


def test_physical_mode_has_explicit_hardware_configuration_gate_and_persistent_faults():
    config = SKETCH.with_name("heater_safety_config.h").read_text(encoding="utf-8")
    assert "DTC_ITOL_DRIVER_TOPOLOGY_VERIFIED 0" in config
    assert "DTC_ITOL_DRIVER_LOGIC_VERIFIED 0" in config
    assert "DTC_ITOL_DRIVER_POLARITY_VERIFIED 0" in config
    assert "DTC_ITOL_INDEPENDENT_OVERTEMP_VERIFIED 0" in config
    assert "DTC_ITOL_PRIMARY_MAX_C 0.0f" in config
    assert "validSafetyConfiguration()" in SOURCE
    assert 'command == \'p\'' in SOURCE
    assert 'command == \'r\'' in SOURCE
    assert "safetyPreferences.putUChar(\"faults\"" in SOURCE
    assert "safetyPreferences.putString(key.c_str(), faultReasonStorage[index])" in SOURCE
    assert "sensor.value >= heater.maxC" in SOURCE


def test_control_logic_covers_invalid_stale_fault_latch_hysteresis_and_minimum_cycle_time():
    assert "sensor.valid" in SOURCE
    config = SKETCH.with_name("heater_safety_config.h").read_text(encoding="utf-8")
    assert "DTC_ITOL_HEATER_HYSTERESIS_C 2.0f" in config
    assert "DTC_ITOL_MIN_SWITCH_INTERVAL_MS 10000UL" in config
    assert "DTC_ITOL_SENSOR_STALE_MS 9000UL" in config
    assert "stale_temperature_data" in SOURCE
    assert "invalid_sensor_reading" in SOURCE
    assert "maximum_temperature_exceeded" in SOURCE
    assert "latchedFaultMask" in SOURCE
    assert "canResetFaults()" in SOURCE
    assert "heaterSamplesSafeAndFresh()" in SOURCE
    assert "!sensor.valid" in SOURCE


def test_monitor_and_dry_run_do_not_call_physical_output_writer():
    commands = SOURCE.split("void handleLocalModeCommands()", 1)[1].split("const char *heaterState", 1)[0]
    monitor_command = commands.split("command == 'm'", 1)[1].split("command == 'd'", 1)[0]
    dry_command = commands.split("command == 'd'", 1)[1].split("command == 'p'", 1)[0]
    assert "makeAllOutputsHighImpedance()" in monitor_command
    assert "makeAllOutputsHighImpedance()" in dry_command
    assert "setPhysicalOutput(" not in monitor_command + dry_command
    writer = SOURCE.split("void setPhysicalOutput", 1)[1].split("void makeAllOutputsHighImpedance", 1)[0]
    assert "operatingMode != PHYSICAL_CONTROL" in writer
    assert "validSafetyConfiguration()" in writer


def test_sensor_errors_remain_null_and_wifi_ota_telemetry_are_preserved():
    assert "disconnected_or_out_of_range" in SOURCE
    assert "location_mapping_unconfigured" in SOURCE
    assert 'payload += "null"' in SOURCE
    assert "provenance\\\":\\\"measured" in SOURCE
    assert "http.POST(payload)" in SOURCE
    assert 'DTC_ITOL_API_URL "http://192.168.0.222:8000/api/v1/telemetry"' in SOURCE
    assert 'DTC_ITOL_API_URL "http://YOUR_LAPTOP_LAN_IP:8000/api/v1/telemetry"' in SKETCH.with_name("secrets.example.h").read_text(encoding="utf-8")
    assert ":8000/api/v1/telemetry" in SOURCE
    telemetry = SOURCE.split("String buildTelemetryJSON()", 1)[1].split("void sendTelemetry", 1)[0]
    assert 'controller_mode' in telemetry and 'wifi_connected' in telemetry
    assert 'wifi_rssi_dbm' in telemetry and 'ip_address' in telemetry
    assert "ArduinoOTA.handle()" in SOURCE
    assert "Wi-Fi disconnected; monitoring continues locally." in SOURCE


def test_private_control_config_is_ignored_and_example_has_only_unverified_defaults():
    ignore_rules = (PROJECT / ".gitignore").read_text(encoding="utf-8")
    assert "firmware/dtc_itol/heater_safety_config.h" in ignore_rules
    example = SKETCH.with_name("heater_safety_config.example.h").read_text(encoding="utf-8")
    assert "VERIFIED 0" in example
    assert "MAX_C 0.0f" in example


def test_secondary2_sensor_validity_crc_missing_and_control_input_contract():
    config = SKETCH.with_name("sensor_addresses.h").read_text(encoding="utf-8").upper()
    assert "DTC_ITOL_DS18B20_SECONDARY2_ROM = {\n    0X28, 0X20, 0X6B, 0X79, 0X00, 0X00, 0X00, 0XF2" in config
    assert "OneWire::crc8(address, 7) == address[7]" in SOURCE
    assert "if (!ds18b20.getAddress(address, i) || !addressValid(address))" in SOURCE

    read_body = SOURCE.split("void readDS18B20()", 1)[1].split("void recordMAX6675Reading", 1)[0]
    assert "DS18B20_ROLE_ADDRESSES[i]" in read_body
    assert "getTempC(*address)" in read_body
    assert "if (!addressValid(*secondary2) || !isDiscoveredAddress(*secondary2))" in read_body
    assert "readings[5].value = NAN" in read_body
    assert "readings[5].valid = false" in read_body
    assert "ds18b20.getTempC(*secondary2)" in read_body
    assert "value != DEVICE_DISCONNECTED_C" in read_body
    assert "readings[5].error = valid ? \"\" : \"disconnected_or_out_of_range\"" in read_body

    assert '{"secondary_heater_2", PIN_SECONDARY_HEATER_2, 5, 100.0f' in SOURCE
    update_body = SOURCE.split("void updateHeaterSafety()", 1)[1].split("void refreshProgressBaselines", 1)[0]
    assert "const Reading &sensor = readings[heater.sensorIndex]" in update_body
    assert "if (!sensor.valid || !isfinite(sensor.value))" in update_body
    assert '"invalid_sensor_reading"' in update_body


def test_secondary2_stale_fault_recovery_requires_valid_fresh_safe_samples_and_local_reset():
    update_body = SOURCE.split("void updateHeaterSafety()", 1)[1].split("void refreshProgressBaselines", 1)[0]
    assert "now - lastSensorSampleAt <= SENSOR_STALE_MS" in update_body
    assert 'latchHeaterFault(i, "stale_temperature_data")' in update_body
    assert "if (latchedFaultMask & (1U << i))" in update_body
    assert "continue;" in update_body.split("if (latchedFaultMask & (1U << i))", 1)[1].split("if (!sampleFresh)", 1)[0]

    reset_body = SOURCE.split("bool canResetFaults()", 1)[1].split("bool heaterSamplesSafeAndFresh()", 1)[0]
    assert "millis() - lastSensorSampleAt > SENSOR_STALE_MS" in reset_body
    assert "for (const Heater &heater : heaters)" in reset_body
    assert "if (!sensor.valid || !isfinite(sensor.value) || sensor.value >= safeResetC) return false" in reset_body
    command_body = SOURCE.split("void handleLocalModeCommands()", 1)[1].split("const char *heaterState", 1)[0]
    reset_command = command_body.split("command == 'r'", 1)[1].split("command == '?'", 1)[0]
    assert "if (canResetFaults())" in reset_command
    assert "latchedFaultMask = 0" in reset_command
    assert "Latched faults explicitly reset after safety checks." in reset_command
    assert "Fault reset denied: resolve faults and verify fresh safe sensor data/configuration first." in reset_command


def test_ota_is_disabled_and_startup_notice_is_printed_once_without_password_gate():
    assert "static constexpr bool OTA_ENABLED = false" in SOURCE
    setup = SOURCE.split("void setup()", 1)[1].split("void loop()", 1)[0]
    assert setup.count("OTA disabled for this USB commissioning build; no OTA password is needed.") == 1
    ota_handler = SOURCE.split("void handleOTA()", 1)[1].split("void readDS18B20()", 1)[0]
    assert "if (!OTA_ENABLED) return;" in ota_handler
    assert "ArduinoOTA.handle()" in ota_handler  # Kept for a later, separately authorized build configuration.
    assert 'Serial.println("OTA disabled: set a non-empty DTC_ITOL_OTA_PASSWORD' not in SOURCE


def test_max6675_fault_and_zero_readings_preserve_library_validity_without_fabricated_zero():
    assert "thermocouplePrimary.readCelsius()" in SOURCE
    assert "thermocoupleSecondary.readCelsius()" in SOURCE
    reader = SOURCE.split("void recordMAX6675Reading", 1)[1].split("void readMAX6675()", 1)[0]
    assert "reading.value = value" in reader
    assert "reading.valid = isfinite(value) && value >= 0.0f && value <= 1024.0f" in reader
    assert 'reading.error = reading.valid ? "" : "open_thermocouple_or_read_fault"' in reader
    assert "MAX6675_library 1.1.2 readCelsius() returns NAN when frame bit D2 is set" in reader
    assert 'else payload += "null"' in SOURCE.split("String buildTelemetryJSON()", 1)[1].split("void sendTelemetry", 1)[0]
    assert "reading.value = 0.0f" not in reader


def test_dry_run_keeps_gpio_outputs_off_and_calculates_heaters_independently():
    assert "OperatingMode operatingMode = MONITOR_ONLY" in SOURCE
    assert '{"primary_mk8", PIN_PRIMARY_HEATER, 3, 150.0f' in SOURCE
    assert '{"secondary_heater_1", PIN_SECONDARY_HEATER_1, 4, 100.0f' in SOURCE
    assert '{"secondary_heater_2", PIN_SECONDARY_HEATER_2, 5, 100.0f' in SOURCE
    update_body = SOURCE.split("void updateHeaterSafety()", 1)[1].split("void refreshProgressBaselines", 1)[0]
    assert "for (uint8_t i = 0; i < 3; ++i)" in update_body
    assert "const Reading &sensor = readings[heater.sensorIndex]" in update_body
    assert "if (operatingMode == PHYSICAL_CONTROL && heater.outputOn != heater.intendedOn" in update_body
    commands = SOURCE.split("void handleLocalModeCommands()", 1)[1].split("const char *heaterState", 1)[0]
    dry_run = commands.split("command == 'd'", 1)[1].split("command == 'p'", 1)[0]
    assert "makeAllOutputsHighImpedance()" in dry_run
    assert "setPhysicalOutput(" not in dry_run
    assert "pinMode(PIN_PRIMARY_HEATER, INPUT)" in SOURCE
    assert "pinMode(PIN_SECONDARY_HEATER_1, INPUT)" in SOURCE
    assert "pinMode(PIN_SECONDARY_HEATER_2, INPUT)" in SOURCE


def test_heater_telemetry_reuses_control_sensor_value_validity_and_provenance():
    telemetry = SOURCE.split("String buildTelemetryJSON()", 1)[1].split("void sendTelemetry", 1)[0]
    assert "const Reading &sensor = readings[heater.sensorIndex]" in telemetry
    assert "if (sensor.valid && isfinite(sensor.value)) payload += String(sensor.value, 2)" in telemetry
    assert 'payload += sensor.valid ? "true" : "false"' in telemetry
    assert 'payload += ",\\"provenance\\":\\"measured\\"' in telemetry
    assert "sensor.sensorRom" in telemetry
