// DTC-ITOL wireless sensor monitor with locally gated heater control; boot is MONITOR_ONLY.
#include <Arduino.h>
#include <ctype.h>
#include <ArduinoOTA.h>
#include <DallasTemperature.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <Preferences.h>
#include <WiFi.h>
#include <max6675.h>
#include <math.h>
#include <string.h>

#include "secrets.h"
#include "sensor_addresses.h"
#include "heater_safety_config.h"

// Explicitly selected for sensor commissioning. No simulated fallback exists.
#define SIMULATION_MODE 0

#ifndef DTC_ITOL_OTA_PASSWORD
#define DTC_ITOL_OTA_PASSWORD ""
#endif

static const char *WIFI_SSID = DTC_ITOL_WIFI_SSID;
static const char *WIFI_PASSWORD = DTC_ITOL_WIFI_PASSWORD;
// Override in the private secrets.h with the laptop's current LAN IPv4 address.
#ifndef DTC_ITOL_API_URL
#define DTC_ITOL_API_URL "http://192.168.0.222:8000/api/v1/telemetry"
#endif
static const char *API_URL = DTC_ITOL_API_URL;
static const char *DEVICE_ID = "dtc-itol-esp32-01";
static constexpr char OTA_HOSTNAME[] = "DTC-ITOL-ESP32";
static constexpr bool OTA_ENABLED = false;  // USB commissioning build; keep wireless OTA disabled.

static constexpr uint8_t PIN_DS18B20 = 4;
static constexpr uint8_t PIN_MAX6675_SCK = 18;
static constexpr uint8_t PIN_MAX6675_SO = 19;
static constexpr uint8_t PIN_MAX6675_CS1 = 22;
static constexpr uint8_t PIN_MAX6675_CS2 = 21;
static constexpr uint8_t PIN_PRIMARY_HEATER = 25;
static constexpr uint8_t PIN_SECONDARY_HEATER_1 = 26; 
static constexpr uint8_t PIN_SECONDARY_HEATER_2 = 27;

static constexpr uint32_t TELEMETRY_INTERVAL_MS = 3000;
static constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 10000;
static constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 12000;
static constexpr uint8_t DS18B20_COUNT = 3;
static constexpr uint8_t MAX_SENSOR_COUNT = 6;
static constexpr uint32_t DS18B20_MAX_C = 125;
static constexpr uint32_t SENSOR_STALE_MS = DTC_ITOL_SENSOR_STALE_MS;
static constexpr uint32_t MIN_SWITCH_INTERVAL_MS = DTC_ITOL_MIN_SWITCH_INTERVAL_MS;
static constexpr float HEATER_HYSTERESIS_C = DTC_ITOL_HEATER_HYSTERESIS_C;

#ifndef DTC_ITOL_DRIVER_TOPOLOGY_VERIFIED
#define DTC_ITOL_DRIVER_TOPOLOGY_VERIFIED 0
#endif
#ifndef DTC_ITOL_DRIVER_LOGIC_VERIFIED
#define DTC_ITOL_DRIVER_LOGIC_VERIFIED 0
#endif
#ifndef DTC_ITOL_DRIVER_POLARITY_VERIFIED
#define DTC_ITOL_DRIVER_POLARITY_VERIFIED 0
#endif
#ifndef DTC_ITOL_INDEPENDENT_OVERTEMP_VERIFIED
#define DTC_ITOL_INDEPENDENT_OVERTEMP_VERIFIED 0
#endif
#ifndef DTC_ITOL_HEATER_OFF_LEVEL
#define DTC_ITOL_HEATER_OFF_LEVEL -1
#endif
#ifndef DTC_ITOL_HEATER_ON_LEVEL
#define DTC_ITOL_HEATER_ON_LEVEL -1
#endif
#ifndef DTC_ITOL_PRIMARY_MAX_C
#define DTC_ITOL_PRIMARY_MAX_C 0.0f
#endif
#ifndef DTC_ITOL_SECONDARY1_MAX_C
#define DTC_ITOL_SECONDARY1_MAX_C 0.0f
#endif
#ifndef DTC_ITOL_SECONDARY2_MAX_C
#define DTC_ITOL_SECONDARY2_MAX_C 0.0f
#endif

enum OperatingMode : uint8_t { MONITOR_ONLY, DRY_RUN, PHYSICAL_CONTROL };
enum HeaterState : uint8_t { HEATER_OFF, HEATER_ON, HEATER_DISABLED, HEATER_FAULT };
struct Heater {
  const char *name;
  uint8_t pin;
  uint8_t sensorIndex;
  float targetC;
  float maxC;
  float progressStartC;
  bool intendedOn;
  bool outputOn;
  uint32_t lastTransitionAt;
  const char *fault;
};
OperatingMode operatingMode = MONITOR_ONLY;
Heater heaters[3] = {
    {"primary_mk8", PIN_PRIMARY_HEATER, 3, 150.0f, DTC_ITOL_PRIMARY_MAX_C, NAN, false, false, 0, nullptr},
    {"secondary_heater_1", PIN_SECONDARY_HEATER_1, 4, 100.0f, DTC_ITOL_SECONDARY1_MAX_C, NAN, false, false, 0, nullptr},
    {"secondary_heater_2", PIN_SECONDARY_HEATER_2, 5, 100.0f, DTC_ITOL_SECONDARY2_MAX_C, NAN, false, false, 0, nullptr},
};
uint32_t lastSensorSampleAt = 0;
uint8_t latchedFaultMask = 0;
Preferences safetyPreferences;
bool safetyPreferencesReady = false;
char faultReasonStorage[3][48] = {{0}, {0}, {0}};

OneWire oneWire(PIN_DS18B20);
DallasTemperature ds18b20(&oneWire);
MAX6675 thermocouplePrimary(PIN_MAX6675_SCK, PIN_MAX6675_CS1, PIN_MAX6675_SO);
MAX6675 thermocoupleSecondary(PIN_MAX6675_SCK, PIN_MAX6675_CS2, PIN_MAX6675_SO);

struct Reading {
  String name;
  float value;
  bool valid;
  String error;
  String sensorRom;
};

Reading readings[MAX_SENSOR_COUNT];
DeviceAddress discoveredAddresses[DS18B20_COUNT];
uint8_t discoveredCount = 0;
uint8_t readingCount = 0;
uint32_t lastTelemetryAt = 0;
uint32_t lastWiFiAttemptAt = 0;
uint32_t wifiAttemptStartedAt = 0;
bool wifiAttemptActive = false;
bool otaStarted = false;

// Sensor roles are assigned by verified ROM configuration, never by scan order.
const char *DS18B20_ROLES[DS18B20_COUNT] = {
    "ds18b20_primary_unassigned",
    "ds18b20_secondary_heater",
    "ds18b20_coolant"
};
const DeviceAddress *DS18B20_ROLE_ADDRESSES[DS18B20_COUNT] = {
    &DTC_ITOL_DS18B20_PRIMARY_ROM,
    &DTC_ITOL_DS18B20_SECONDARY_ROM,
    &DTC_ITOL_DS18B20_COOLANT_ROM
};

bool isZeroAddress(const DeviceAddress address) {
  for (uint8_t i = 0; i < 8; ++i) {
    if (address[i] != 0) return false;
  }
  return true;
}

bool addressValid(const DeviceAddress address) {
  return OneWire::crc8(address, 7) == address[7];
}

String addressHex(const DeviceAddress address) {
  String result;
  result.reserve(16);
  for (uint8_t i = 0; i < 8; ++i) {
    if (address[i] < 16) result += '0';
    result += String(address[i], HEX);
  }
  result.toUpperCase();
  return result;
}

void initializeSensors() {
  ds18b20.begin();
  ds18b20.setResolution(12);
  ds18b20.setWaitForConversion(true);
  scanDS18B20Addresses();
}

void scanDS18B20Addresses() {
  discoveredCount = 0;
  const uint8_t found = static_cast<uint8_t>(ds18b20.getDeviceCount());
  Serial.printf("DS18B20 discovery: library reports %u device(s).\n", found);

  for (uint8_t i = 0; i < found && discoveredCount < DS18B20_COUNT; ++i) {
    DeviceAddress address;
    if (!ds18b20.getAddress(address, i) || !addressValid(address)) {
      Serial.printf("DS18B20 discovery slot %u: missing address or invalid ROM CRC.\n", i);
      continue;
    }
    memcpy(discoveredAddresses[discoveredCount], address, sizeof(DeviceAddress));
    Serial.printf("DS18B20 discovered ROM=%s\n",
                  addressHex(discoveredAddresses[discoveredCount]).c_str());
    ++discoveredCount;
  }

  for (uint8_t role = 0; role < DS18B20_COUNT; ++role) {
    readings[role].name = DS18B20_ROLES[role];
    readings[role].value = NAN;
    readings[role].valid = false;
    readings[role].sensorRom = "";
    const DeviceAddress *address = DS18B20_ROLE_ADDRESSES[role];
    if (!address || isZeroAddress(*address)) {
      readings[role].error = "location_mapping_unconfigured";
    } else {
      readings[role].sensorRom = addressHex(*address);
      readings[role].error = "sensor_not_discovered";
    }
  }

  readingCount = 6;
  readings[3] = {"thermocouple_primary", NAN, false, "not_read", ""};
  readings[4] = {"thermocouple_secondary", NAN, false, "not_read", ""};

  readings[5] = {"ds18b20_secondary_heater_2", NAN, false,
                 "sensor_not_discovered", addressHex(DTC_ITOL_DS18B20_SECONDARY2_ROM)};

  if (discoveredCount != DS18B20_COUNT) {
    Serial.printf("Sensor discovery warning: expected 3 DS18B20 sensors, found %u.\n",
                  discoveredCount);
  }
}

bool isDiscoveredAddress(const DeviceAddress address) {
  for (uint8_t i = 0; i < discoveredCount; ++i) {
    if (memcmp(discoveredAddresses[i], address, sizeof(DeviceAddress)) == 0) return true;
  }
  return false;
}

void initializeWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  lastWiFiAttemptAt = millis() - WIFI_RETRY_INTERVAL_MS;
  reconnectWiFi();
}

void reconnectWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiAttemptActive = false;
    return;
  }
  const uint32_t now = millis();
  if (wifiAttemptActive && now - wifiAttemptStartedAt < WIFI_CONNECT_TIMEOUT_MS) return;
  if (now - lastWiFiAttemptAt < WIFI_RETRY_INTERVAL_MS) return;

  Serial.println("Starting bounded Wi-Fi connection attempt; local sensor monitoring continues.");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWiFiAttemptAt = now;
  wifiAttemptStartedAt = now;
  wifiAttemptActive = true;
}

void initializeOTA() {
  if (!OTA_ENABLED || otaStarted || WiFi.status() != WL_CONNECTED) return;
  if (strlen(DTC_ITOL_OTA_PASSWORD) == 0 ||
      strcmp(DTC_ITOL_OTA_PASSWORD, "SET_A_LONG_UNIQUE_OTA_PASSWORD") == 0) {
    return;
  }

  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.setPassword(DTC_ITOL_OTA_PASSWORD);
  ArduinoOTA.onStart([]() { Serial.println("OTA update started."); });
  ArduinoOTA.onEnd([]() { Serial.println("OTA update complete."); });
  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    static uint32_t lastProgressLogAt = 0;
    if (millis() - lastProgressLogAt >= 500 || progress == total) {
      const unsigned int percent = total ? (progress * 100U) / total : 0;
      Serial.printf("OTA progress: %u%%\n", percent);
      lastProgressLogAt = millis();
    }
  });
  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA error code %u.\n", static_cast<unsigned int>(error));
  });
  ArduinoOTA.begin();
  otaStarted = true;
  Serial.printf("OTA ready: %s at %s, RSSI %d dBm. Password is configured and hidden.\n",
                OTA_HOSTNAME, WiFi.localIP().toString().c_str(), WiFi.RSSI());
}

void handleOTA() {
  if (!OTA_ENABLED) return;
  if (WiFi.status() != WL_CONNECTED) {
    otaStarted = false;
    return;
  }
  initializeOTA();
  if (otaStarted) ArduinoOTA.handle();
}

void readDS18B20() {
  ds18b20.requestTemperatures();

  for (uint8_t i = 0; i < DS18B20_COUNT; ++i) {
    const DeviceAddress *address = DS18B20_ROLE_ADDRESSES[i];
    if (!address || isZeroAddress(*address)) {
      readings[i].value = NAN;
      readings[i].valid = false;
      readings[i].error = "location_mapping_unconfigured";
      continue;
    }
    if (!addressValid(*address) || !isDiscoveredAddress(*address)) {
      readings[i].value = NAN;
      readings[i].valid = false;
      readings[i].error = addressValid(*address) ? "sensor_not_discovered" : "invalid_rom_crc";
      Serial.printf("[SENSOR FAULT] %s (ROM %s): configured DS18B20 missing or ROM CRC invalid; telemetry value will be null.\n",
                    readings[i].name.c_str(), readings[i].sensorRom.c_str());
      continue;
    }

    const float value = ds18b20.getTempC(*address);
    const bool valid = isfinite(value) && value != DEVICE_DISCONNECTED_C &&
                       value >= -55.0f && value <= DS18B20_MAX_C;
    readings[i].value = valid ? value : NAN;
    readings[i].valid = valid;
    readings[i].error = valid ? "" : "disconnected_or_out_of_range";

    if (!valid) {
      Serial.printf("[SENSOR FAULT] %s (ROM %s): invalid DS18B20 reading; value will be null.\n",
                    readings[i].name.c_str(), readings[i].sensorRom.c_str());
    }
  }

  const DeviceAddress *secondary2 = &DTC_ITOL_DS18B20_SECONDARY2_ROM;
  if (!addressValid(*secondary2) || !isDiscoveredAddress(*secondary2)) {
    readings[5].value = NAN;
    readings[5].valid = false;
    readings[5].error = addressValid(*secondary2) ? "sensor_not_discovered" : "invalid_rom_crc";
    Serial.printf("[SENSOR FAULT] %s (ROM %s): configured DS18B20 missing or ROM CRC invalid; telemetry value will be null.\n",
                  readings[5].name.c_str(), readings[5].sensorRom.c_str());
  } else {
    const float value = ds18b20.getTempC(*secondary2);
    const bool valid = isfinite(value) && value != DEVICE_DISCONNECTED_C &&
                       value >= -55.0f && value <= DS18B20_MAX_C;
    readings[5].value = valid ? value : NAN;
    readings[5].valid = valid;
    readings[5].error = valid ? "" : "disconnected_or_out_of_range";
    if (!valid) {
      Serial.printf("[SENSOR FAULT] %s (ROM %s): invalid DS18B20 reading; value will be null.\n",
                    readings[5].name.c_str(), readings[5].sensorRom.c_str());
    }
  }
}

void recordMAX6675Reading(Reading &reading, float value) {
  reading.value = value;
  reading.valid = isfinite(value) && value >= 0.0f && value <= 1024.0f;
  reading.error = reading.valid ? "" : "open_thermocouple_or_read_fault";
  if (!reading.valid) {
    // MAX6675_library 1.1.2 readCelsius() returns NAN when frame bit D2 is set.
    // Preserve that invalid value; never turn it into a fabricated zero.
    Serial.printf("[SENSOR FAULT] %s: MAX6675 open-thermocouple fault bit/read fault or out-of-range value; telemetry value will be null.\n",
                  reading.name.c_str());
  }
}

void readMAX6675() {
  recordMAX6675Reading(readings[3], thermocouplePrimary.readCelsius());
  recordMAX6675Reading(readings[4], thermocoupleSecondary.readCelsius());
}

void validateSensorReadings() {
  // Individual drivers validate during acquisition; this pass enforces null-safe output.
  for (uint8_t i = 0; i < readingCount; ++i) {
    if (!readings[i].valid || !isfinite(readings[i].value)) readings[i].valid = false;
  }
}

const char *modeName() {
  switch (operatingMode) {
    case DRY_RUN: return "DRY_RUN";
    case PHYSICAL_CONTROL: return "PHYSICAL_CONTROL";
    default: return "MONITOR_ONLY";
  }
}

bool validSafetyConfiguration() {
  // This uploaded build is for sensor-mapping commissioning only.
  // Physical heater control must not be enabled by this firmware revision.
  return false;
}

void setPhysicalOutput(uint8_t index, bool enabled) {
  if (operatingMode != PHYSICAL_CONTROL || !validSafetyConfiguration()) return;
  Heater &heater = heaters[index];
  digitalWrite(heater.pin, enabled ? DTC_ITOL_HEATER_ON_LEVEL : DTC_ITOL_HEATER_OFF_LEVEL);
  pinMode(heater.pin, OUTPUT);
  heater.outputOn = enabled;
  heater.lastTransitionAt = millis();
}

void makeAllOutputsHighImpedance() {
  if (operatingMode == PHYSICAL_CONTROL && validSafetyConfiguration()) {
    for (uint8_t i = 0; i < 3; ++i) {
      digitalWrite(heaters[i].pin, DTC_ITOL_HEATER_OFF_LEVEL);
      pinMode(heaters[i].pin, INPUT);
      heaters[i].outputOn = false;
    }
  } else {
    for (uint8_t i = 0; i < 3; ++i) {
      pinMode(heaters[i].pin, INPUT);
      heaters[i].outputOn = false;
    }
  }
}

void latchHeaterFault(uint8_t index, const char *reason) {
  strncpy(faultReasonStorage[index], reason, sizeof(faultReasonStorage[index]) - 1);
  faultReasonStorage[index][sizeof(faultReasonStorage[index]) - 1] = '\0';
  heaters[index].fault = faultReasonStorage[index];
  heaters[index].intendedOn = false;
  latchedFaultMask |= static_cast<uint8_t>(1U << index);
  if (safetyPreferencesReady) {
    safetyPreferences.putUChar("faults", latchedFaultMask);
    const String key = String("fault") + String(index);
    safetyPreferences.putString(key.c_str(), faultReasonStorage[index]);
  }
  if (operatingMode == PHYSICAL_CONTROL) setPhysicalOutput(index, false);
}

void updateHeaterSafety() {
  const uint32_t now = millis();
  const bool sampleFresh = lastSensorSampleAt && now - lastSensorSampleAt <= SENSOR_STALE_MS;
  for (uint8_t i = 0; i < 3; ++i) {
    Heater &heater = heaters[i];
    const Reading &sensor = readings[heater.sensorIndex];
    if (latchedFaultMask & (1U << i)) {
      heater.intendedOn = false;
      if (operatingMode == PHYSICAL_CONTROL) setPhysicalOutput(i, false);
      continue;
    }
    if (!sampleFresh) { latchHeaterFault(i, "stale_temperature_data"); continue; }
    if (!sensor.valid || !isfinite(sensor.value)) {
      const char *reason = sensor.error == "location_mapping_unconfigured"
                               ? "sensor_rom_mapping_unconfigured"
                               : (sensor.error == "sensor_not_discovered"
                                      ? "sensor_disconnected" : "invalid_sensor_reading");
      latchHeaterFault(i, reason);
      continue;
    }
    const bool maxLimitConfigured = heater.maxC > heater.targetC;
    if (maxLimitConfigured && sensor.value >= heater.maxC) {
      latchHeaterFault(i, "maximum_temperature_exceeded");
      continue;
    }
    if (!maxLimitConfigured) {
      heater.fault = "maximum_temperature_limit_unconfigured";
      if (operatingMode == MONITOR_ONLY || operatingMode == PHYSICAL_CONTROL) {
        heater.intendedOn = false;
        if (operatingMode == PHYSICAL_CONTROL) setPhysicalOutput(i, false);
        continue;
      }
    } else if (heater.fault && strcmp(heater.fault, "maximum_temperature_limit_unconfigured") == 0) {
      heater.fault = nullptr;
    }

    if (heater.intendedOn) {
      if (sensor.value >= heater.targetC + HEATER_HYSTERESIS_C) heater.intendedOn = false;
    } else if (sensor.value <= heater.targetC - HEATER_HYSTERESIS_C) {
      heater.intendedOn = true;
    }

    if (operatingMode == PHYSICAL_CONTROL && heater.outputOn != heater.intendedOn &&
        now - heater.lastTransitionAt >= MIN_SWITCH_INTERVAL_MS) {
      setPhysicalOutput(i, heater.intendedOn);
    }
  }
}

void refreshProgressBaselines(bool reset = false) {
  for (Heater &heater : heaters) {
    const Reading &sensor = readings[heater.sensorIndex];
    if (sensor.valid && isfinite(sensor.value) && (reset || !isfinite(heater.progressStartC))) {
      heater.progressStartC = sensor.value;
    }
  }
}

bool canResetFaults() {
  if (!lastSensorSampleAt || millis() - lastSensorSampleAt > SENSOR_STALE_MS) return false;
  for (const Heater &heater : heaters) {
    const Reading &sensor = readings[heater.sensorIndex];
    const float safeResetC = heater.maxC > heater.targetC
                                 ? heater.maxC - HEATER_HYSTERESIS_C
                                 : heater.targetC - HEATER_HYSTERESIS_C;
    if (!sensor.valid || !isfinite(sensor.value) || sensor.value >= safeResetC) return false;
  }
  return true;
}

bool heaterSamplesSafeAndFresh() {
  if (!lastSensorSampleAt || millis() - lastSensorSampleAt > SENSOR_STALE_MS) return false;
  for (const Heater &heater : heaters) {
    const Reading &sensor = readings[heater.sensorIndex];
    if (!sensor.valid || !isfinite(sensor.value) || heater.maxC <= heater.targetC ||
        sensor.value >= heater.maxC) return false;
  }
  return true;
}

void handleLocalModeCommands() {
  while (Serial.available()) {
    const char command = static_cast<char>(tolower(Serial.read()));
    if (command == 'm') {
      makeAllOutputsHighImpedance();
      operatingMode = MONITOR_ONLY;
      refreshProgressBaselines(true);
      Serial.println("Operating mode: MONITOR_ONLY; heater outputs disabled.");
    } else if (command == 'd') {
      makeAllOutputsHighImpedance();
      operatingMode = DRY_RUN;
      refreshProgressBaselines(true);
      Serial.println("Operating mode: DRY_RUN; intended states are telemetry only.");
    } else if (command == 'p') {
      Serial.println("PHYSICAL_CONTROL locked: this commissioning build is MONITOR_ONLY/DRY_RUN only.");
    } else if (command == 'r') {
      if (canResetFaults()) {
        latchedFaultMask = 0;
        if (safetyPreferencesReady) {
          safetyPreferences.putUChar("faults", 0);
          for (uint8_t i = 0; i < 3; ++i) {
            const String key = String("fault") + String(i);
            safetyPreferences.remove(key.c_str());
          }
        }
        for (uint8_t i = 0; i < 3; ++i) {
          faultReasonStorage[i][0] = '\0';
          heaters[i].fault = nullptr;
        }
        Serial.println("Latched faults explicitly reset after safety checks.");
      } else Serial.println("Fault reset denied: resolve faults and verify fresh safe sensor data/configuration first.");
    } else if (command == '?') {
      Serial.println("Local commands: m=MONITOR_ONLY, d=DRY_RUN, p=locked physical-control request, r=reset resolved latched fault.");
    }
  }
}

const char *heaterState(const Heater &heater) {
  if (latchedFaultMask & (1U << (&heater - heaters))) return "FAULT";
  if (heater.fault) return "DISABLED";
  if (operatingMode == MONITOR_ONLY) return "DISABLED";
  if (operatingMode == DRY_RUN) return "DISABLED";
  return heater.outputOn ? "ON" : "OFF";
}

String jsonEscape(const String &input) {
  String escaped;
  for (size_t i = 0; i < input.length(); ++i) {
    const char c = input[i];
    if (c == '"' || c == '\\') escaped += '\\';
    if (c == '\n') escaped += "\\n";
    else if (c == '\r') escaped += "\\r";
    else escaped += c;
  }
  return escaped;
}

String buildTelemetryJSON() {
  String payload;
  payload.reserve(1500);
  payload += "{\"device_id\":\"" + jsonEscape(DEVICE_ID) +
             "\",\"mode\":\"monitoring\",\"controller_mode\":\"" + String(modeName()) +
             "\",\"wifi_connected\":";
  payload += WiFi.status() == WL_CONNECTED ? "true" : "false";
  payload += ",\"wifi_rssi_dbm\":";
  if (WiFi.status() == WL_CONNECTED) payload += String(WiFi.RSSI());
  else payload += "null";
  payload += ",\"ip_address\":";
  if (WiFi.status() == WL_CONNECTED) {
    payload += "\"" + jsonEscape(WiFi.localIP().toString()) + "\"";
  } else {
    payload += "null";
  }
  payload += ",\"sensors\":[";
  for (uint8_t i = 0; i < readingCount; ++i) {
    if (i) payload += ',';
    payload += "{\"name\":\"" + jsonEscape(readings[i].name) + "\",\"value\":";
    if (readings[i].valid && isfinite(readings[i].value)) payload += String(readings[i].value, 2);
    else payload += "null";
    payload += ",\"unit\":\"°C\",\"valid\":";
    payload += readings[i].valid ? "true" : "false";
    payload += ",\"provenance\":\"measured\"";
    if (readings[i].sensorRom.length()) {
      payload += ",\"sensor_rom\":\"" + jsonEscape(readings[i].sensorRom) + "\"";
    }
    payload += ",\"error\":\"" + jsonEscape(readings[i].error) + "\"}";
  }
  for (uint8_t i = 0; i < 3; ++i) {
    const Heater &heater = heaters[i];
    const Reading &sensor = readings[heater.sensorIndex];
    float progress = NAN;
    if (sensor.valid && isfinite(sensor.value) && isfinite(heater.progressStartC)) {
      progress = heater.progressStartC >= heater.targetC
          ? 100.0f
          : fminf(100.0f, fmaxf(0.0f,
              ((sensor.value - heater.progressStartC) /
               (heater.targetC - heater.progressStartC)) * 100.0f));
    }
    payload += ", {\"name\":\"" + String(heater.name) + "\",\"kind\":\"heater_state\",\"value\":";
    if (sensor.valid && isfinite(sensor.value)) payload += String(sensor.value, 2);
    else payload += "null";
    payload += ",\"unit\":\"°C\",\"valid\":";
    payload += sensor.valid ? "true" : "false";
    payload += ",\"provenance\":\"measured\",\"target_c\":" + String(heater.targetC, 1) +
               ",\"state\":\"" + String(heaterState(heater)) + "\",\"intended_state\":\"" +
               String(heater.intendedOn ? "ON" : "OFF") + "\",\"control_mode\":\"" + String(modeName()) +
               "\",\"progress_pct\":";
    if (isfinite(progress)) payload += String(progress, 1); else payload += "null";
    if (sensor.sensorRom.length()) {
      payload += ",\"sensor_rom\":\"" + jsonEscape(sensor.sensorRom) + "\"";
    }
    const String heaterError = heater.fault ? String(heater.fault) : sensor.error;
    payload += ",\"error\":\"" + jsonEscape(heaterError) + "\"}";
  }
  payload += "]}";
  return payload;
}

void sendTelemetry(const String &payload) {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.setTimeout(250);
  if (!http.begin(API_URL)) {
    Serial.println("Telemetry HTTP setup failed; local monitoring continues.");
    return;
  }
  http.addHeader("Content-Type", "application/json");
  const int status = http.POST(payload);
  if (status < 0) Serial.printf("Telemetry transport error: %s\n", http.errorToString(status).c_str());
  else if (status < 200 || status >= 300) Serial.printf("Telemetry endpoint returned HTTP %d.\n", status);
  else Serial.printf("Telemetry accepted: HTTP %d.\n", status);
  http.end();
}

void printSystemStatus() {
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("Wi-Fi connected: IP %s, RSSI %d dBm.\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    Serial.println("Wi-Fi disconnected; monitoring continues locally.");
  }
  for (uint8_t i = 0; i < readingCount; ++i) {
    if (readings[i].valid) {
      Serial.printf("%s: %.2f °C [MEASURED]\n", readings[i].name.c_str(), readings[i].value);
    } else {
      Serial.printf("%s: INVALID [MEASURED, %s]\n", readings[i].name.c_str(), readings[i].error.c_str());
    }
  }
  Serial.printf("Operating mode: %s. Peltier control is unassigned and disabled.\n", modeName());
  for (const Heater &heater : heaters) {
    Serial.printf("%s: state=%s intended=%s target=%.1f°C%s%s\n", heater.name,
                  heaterState(heater), heater.intendedOn ? "ON" : "OFF", heater.targetC,
                  heater.fault ? " fault=" : "", heater.fault ? heater.fault : "");
  }
  Serial.println("DS18B20 rated range ends at 125 °C; use primary MAX6675 for high-temperature monitoring.");
}

void setup() {
  Serial.begin(115200);
  safetyPreferencesReady = safetyPreferences.begin("dtcSafety", false);
  if (safetyPreferencesReady) {
    latchedFaultMask = safetyPreferences.getUChar("faults", 0) & 0x07;
    for (uint8_t i = 0; i < 3; ++i) {
      if (latchedFaultMask & (1U << i)) {
        const String key = String("fault") + String(i);
        safetyPreferences.getString(key.c_str(), faultReasonStorage[i], sizeof(faultReasonStorage[i]));
        heaters[i].fault = faultReasonStorage[i][0] ? faultReasonStorage[i] : "latched_fault_after_restart";
      }
    }
  }
  pinMode(PIN_PRIMARY_HEATER, INPUT);
  pinMode(PIN_SECONDARY_HEATER_1, INPUT);
  pinMode(PIN_SECONDARY_HEATER_2, INPUT);

  initializeSensors();
  initializeWiFi();
  Serial.printf("DTC-ITOL started; simulation=%d; mode=MONITOR_ONLY; heater outputs disabled.\n", SIMULATION_MODE);
  Serial.println("OTA disabled for this USB commissioning build; no OTA password is needed.");
  Serial.println("Type ? in Serial Monitor for local mode and fault-reset commands. Physical mode requires reviewed safety configuration.");
  Serial.println("DS18B20 mapping uses verified ROMs: AB=secondary heater 1 monitor, B3=coolant, F2=secondary heater 2.");
  Serial.println("Primary high-temperature channel is MAX6675; never use DS18B20 to validate 150 °C.");
  Serial.println("Actuator control remains disabled; configured sensor readings are sent to telemetry.");
}

void loop() {
  handleLocalModeCommands();
  handleOTA();
  reconnectWiFi();

  const uint32_t now = millis();
  if (lastSensorSampleAt) updateHeaterSafety();
  if (now - lastTelemetryAt < TELEMETRY_INTERVAL_MS) return;
  lastTelemetryAt = now;

  readDS18B20();
  readMAX6675();
  validateSensorReadings();
  refreshProgressBaselines();
  lastSensorSampleAt = millis();
  updateHeaterSafety();
  printSystemStatus();
  const String payload = buildTelemetryJSON();
  sendTelemetry(payload);
  updateHeaterSafety();
  handleOTA();
}
