#pragma once

// Copy to heater_safety_config.h only after the corresponding facts are verified.
// Defaults intentionally prevent PHYSICAL_CONTROL.
#define DTC_ITOL_DRIVER_TOPOLOGY_VERIFIED 0
#define DTC_ITOL_DRIVER_LOGIC_VERIFIED 0
#define DTC_ITOL_DRIVER_POLARITY_VERIFIED 0
#define DTC_ITOL_INDEPENDENT_OVERTEMP_VERIFIED 0
// Use 0 or 1 only after verifying actual inactive/active logic levels for all drivers.
#define DTC_ITOL_HEATER_OFF_LEVEL -1
#define DTC_ITOL_HEATER_ON_LEVEL -1
#define DTC_ITOL_HEATER_HYSTERESIS_C 2.0f
#define DTC_ITOL_MIN_SWITCH_INTERVAL_MS 10000UL
#define DTC_ITOL_SENSOR_STALE_MS 9000UL
// Set limits from a reviewed thermal safety plan; zero means unset and blocks control.
#define DTC_ITOL_PRIMARY_MAX_C 0.0f
#define DTC_ITOL_SECONDARY1_MAX_C 0.0f
#define DTC_ITOL_SECONDARY2_MAX_C 0.0f
