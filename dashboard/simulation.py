"""Smooth, isolated DTC-ITOL demonstration simulation (never sent to the API)."""

import math


TARGETS = {"primary": 150.0, "secondary": 100.0}
AMBIENT_C = 25.0
HOLD_SECONDS = 3.0
FAN_RESPONSE_SECONDS = 6.0
MAX_HISTORY = 1800


def create_simulation(now_monotonic: float) -> dict:
    state = {
        "elapsed": 0.0,
        "last_wall": now_monotonic,
        "paused": False,
        "speed": 1.0,
        "timestep_s": 0.1,
        "primary": _new_heater(AMBIENT_C, TARGETS["primary"], 3.0, 0.30, 0.08, 0.82),
        "secondary": _new_heater(AMBIENT_C, TARGETS["secondary"], 2.5, 0.25, 0.08, 0.68),
        "secondary_tc_c": AMBIENT_C,
        "coolant_c": AMBIENT_C,
        "cpu_c": AMBIENT_C,
        "cpu_fan_rpm": 500.0,
        "secondary_fan_rpm": 450.0,
        "events": [{"elapsed": 0.0, "message": "Thermal profile initialized."}],
        "history": [],
    }
    append_history(state)
    return state


def _new_heater(temp: float, target: float, max_rise_rate: float, initial_rise_rate: float,
                acceleration: float, cool_rate: float) -> dict:
    return {
        "temperature_c": temp,
        "target_c": target,
        "rise_rate_c_s": max_rise_rate,
        "initial_rise_rate_c_s": initial_rise_rate,
        "current_rise_rate_c_s": initial_rise_rate,
        "acceleration_c_s2": acceleration,
        "cool_rate_c_s": cool_rate,
        "phase": "HEATING",
        "hold_remaining_s": HOLD_SECONDS,
        "fan_response_remaining_s": FAN_RESPONSE_SECONDS,
    }


def _event(state: dict, elapsed: float, text: str) -> None:
    state["events"].append({"elapsed": max(0.0, elapsed), "message": text})
    state["events"] = state["events"][-80:]


def _advance_heater(heater: dict, dt: float, start_elapsed: float, name: str, state: dict) -> None:
    remaining = dt
    consumed = 0.0
    while remaining > 1e-9:
        phase = heater["phase"]
        if phase == "HEATING":
            distance = max(0.0, heater["target_c"] - heater["temperature_c"])
            rate = heater["current_rise_rate_c_s"]
            acceleration = heater["acceleration_c_s2"]
            if acceleration > 0:
                seconds_to_target = max(0.0, (math.sqrt(rate * rate + 2 * acceleration * distance) - rate) / acceleration)
            else:
                seconds_to_target = distance / max(rate, 1e-9)
            if seconds_to_target > remaining:
                heater["temperature_c"] += rate * remaining + 0.5 * acceleration * remaining * remaining
                heater["current_rise_rate_c_s"] = min(heater["rise_rate_c_s"], rate + acceleration * remaining)
                return
            heater["temperature_c"] = heater["target_c"]
            heater["current_rise_rate_c_s"] = min(heater["rise_rate_c_s"], rate + acceleration * seconds_to_target)
            remaining -= seconds_to_target
            consumed += seconds_to_target
            heater["phase"] = "HOLD"
            heater["hold_remaining_s"] = HOLD_SECONDS
            _event(state, start_elapsed + consumed, f"{name} target {heater['target_c']:.0f}°C reached; 3-second threshold hold started.")
        elif phase == "HOLD":
            hold_step = min(remaining, heater["hold_remaining_s"])
            heater["temperature_c"] = heater["target_c"]
            heater["hold_remaining_s"] -= hold_step
            remaining -= hold_step
            consumed += hold_step
            if heater["hold_remaining_s"] <= 1e-9:
                heater["hold_remaining_s"] = 0.0
                heater["phase"] = "FAN RESPONSE"
                heater["fan_response_remaining_s"] = FAN_RESPONSE_SECONDS
                _event(state, start_elapsed + consumed, f"{name} threshold dwell complete; fan response increased.")
        elif phase == "FAN RESPONSE":
            phase_step = min(remaining, heater["fan_response_remaining_s"])
            heater["fan_response_remaining_s"] -= phase_step
            remaining -= phase_step
            consumed += phase_step
            if heater["fan_response_remaining_s"] <= 1e-9:
                heater["fan_response_remaining_s"] = 0.0
                heater["phase"] = "COOLING"
                _event(state, start_elapsed + consumed, f"{name} cooling phase started.")
        else:  # COOLING
            seconds_to_ambient = max(0.0, (heater["temperature_c"] - AMBIENT_C) / heater["cool_rate_c_s"])
            if seconds_to_ambient > remaining:
                heater["temperature_c"] -= heater["cool_rate_c_s"] * remaining
                return
            heater["temperature_c"] = AMBIENT_C
            heater["current_rise_rate_c_s"] = heater["initial_rise_rate_c_s"]
            remaining -= seconds_to_ambient
            consumed += seconds_to_ambient
            heater["phase"] = "HEATING"
            _event(state, start_elapsed + consumed, f"{name} returned to ambient; a new thermal cycle started.")


def _approach(current: float, target: float, maximum_change: float) -> float:
    difference = target - current
    return current + max(-maximum_change, min(maximum_change, difference))


def advance_simulation(state: dict, now_monotonic: float) -> dict:
    """Advance thermal states smoothly by wall time times the selected simulation speed."""
    last_wall = float(state.get("last_wall", now_monotonic))
    wall_dt = max(0.0, now_monotonic - last_wall)
    state["last_wall"] = now_monotonic
    if state.get("paused") or wall_dt <= 0:
        return state

    simulated_dt = wall_dt * float(state.get("speed", 1.0))
    # Small integration steps keep dependent thermal masses and fan motors smooth.
    timestep = max(0.05, float(state.get("timestep_s", 0.1)))
    steps = max(1, math.ceil(simulated_dt / timestep))
    step_dt = simulated_dt / steps
    for _ in range(steps):
        cursor = state["elapsed"]
        _advance_heater(state["primary"], step_dt, cursor, "Primary", state)
        _advance_heater(state["secondary"], step_dt, cursor, "Secondary", state)
        state["elapsed"] += step_dt

        primary_c = state["primary"]["temperature_c"]
        secondary_c = state["secondary"]["temperature_c"]
        coolant_target = AMBIENT_C + 0.055 * (primary_c - AMBIENT_C) + 0.095 * (secondary_c - AMBIENT_C)
        cpu_target = AMBIENT_C + 0.105 * (primary_c - AMBIENT_C) + 0.075 * (secondary_c - AMBIENT_C)
        state["coolant_c"] += (coolant_target - state["coolant_c"]) * (1.0 - math.exp(-step_dt / 13.0))
        state["cpu_c"] += (cpu_target - state["cpu_c"]) * (1.0 - math.exp(-step_dt / 7.0))
        state["secondary_tc_c"] += (secondary_c - state["secondary_tc_c"]) * (1.0 - math.exp(-step_dt / 2.5))

        cpu_fan_target = min(1900.0, 500.0 + max(0.0, state["cpu_c"] - AMBIENT_C) * 48.0)
        secondary_fan_target = 450.0
        if state["secondary"]["phase"] in {"FAN RESPONSE", "COOLING"}:
            secondary_fan_target = min(1650.0, 450.0 + max(0.0, secondary_c - AMBIENT_C) * 24.0)
        state["cpu_fan_rpm"] = _approach(state["cpu_fan_rpm"], cpu_fan_target, 220.0 * step_dt)
        state["secondary_fan_rpm"] = _approach(state["secondary_fan_rpm"], secondary_fan_target, 260.0 * step_dt)

    append_history(state)
    return state


def append_history(state: dict) -> None:
    sample = {
        "elapsed": state["elapsed"],
        "primary_c": state["primary"]["temperature_c"],
        "secondary_c": state["secondary"]["temperature_c"],
        "coolant_c": state["coolant_c"],
        "cpu_c": state["cpu_c"],
        "secondary_tc_c": state["secondary_tc_c"],
        "cpu_fan_rpm": state["cpu_fan_rpm"],
        "secondary_fan_rpm": state["secondary_fan_rpm"],
    }
    history = state.setdefault("history", [])
    if history and abs(history[-1]["elapsed"] - sample["elapsed"]) < 0.05:
        history[-1] = sample
    else:
        history.append(sample)
    del history[:-MAX_HISTORY]


def elapsed_label(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60:02d}:{whole % 60:02d}"


def trend_label(history: list[dict], key: str) -> str:
    if len(history) < 2:
        return "Stable →"
    difference = history[-1][key] - history[-2][key]
    if difference > 0.02:
        return "Rising ↑"
    if difference < -0.02:
        return "Falling ↓"
    return "Stable →"
