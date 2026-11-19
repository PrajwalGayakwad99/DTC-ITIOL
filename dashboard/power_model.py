"""Read-only estimates from configured supply and load nameplate values."""

SUPPLIES = {
    "SMPS 1": {"voltage_v": 12.0, "current_a": 10.0, "capacity_w": 120.0},
    "SMPS 2": {"voltage_v": 12.0, "current_a": 10.0, "capacity_w": 120.0},
}


def estimate_power(
    primary_duty: float,
    secondary_duty: float,
    peltier_duty: float,
    cpu_fan_current_a: float | None,
    brushless_fan_current_a: float | None,
    load_ratings_w: dict[str, float | None] | None = None,
) -> dict:
    """Estimate configured loads only; unspecified nameplate values stay unknown."""
    primary = max(0.0, min(100.0, float(primary_duty))) / 100.0
    secondary = max(0.0, min(100.0, float(secondary_duty))) / 100.0
    peltier = max(0.0, min(100.0, float(peltier_duty))) / 100.0
    ratings = load_ratings_w or {}

    def configured_load(name: str, rating_key: str, duty: float) -> dict:
        raw_rating = ratings.get(rating_key)
        rating = float(raw_rating) if raw_rating is not None and float(raw_rating) > 0 else None
        return {
            "load": name,
            "rated_w": rating,
            "duty_pct": duty * 100 if rating is not None else None,
            "estimated_w": rating * duty if rating is not None else None,
            "configured": rating is not None,
        }

    loads1 = [
        configured_load("R385 pump", "pump", 1.0),
        configured_load("Primary cartridge heater", "primary_heater", primary),
        configured_load("Peltier module", "peltier", peltier),
    ]
    loads2 = [
        configured_load("Secondary cartridge heater 1", "secondary_heater_1", secondary),
        configured_load("Secondary cartridge heater 2", "secondary_heater_2", secondary),
    ]
    for label, current in (("CPU tower fan", cpu_fan_current_a), ("12V brushless fan", brushless_fan_current_a)):
        configured = current is not None and float(current) > 0
        watts = float(current) * 12.0 if configured else None
        loads2.append({
            "load": label,
            "rated_w": watts,
            "duty_pct": 100.0 if configured else None,
            "estimated_w": watts,
            "configured": configured,
        })
    supplies = []
    for name, loads in (("SMPS 1", loads1), ("SMPS 2", loads2)):
        known_demand = sum(item["estimated_w"] or 0.0 for item in loads)
        capacity = SUPPLIES[name]["capacity_w"]
        supplies.append({
            "name": name,
            "capacity_w": capacity,
            "known_demand_w": known_demand,
            "headroom_w": capacity - known_demand,
            "utilization_pct": known_demand / capacity * 100.0,
            "overload": known_demand > capacity,
            "unknown_loads": [item["load"] for item in loads if not item["configured"]],
            "loads": loads,
        })
    return {
        "supplies": supplies,
        "interlock_conflict": secondary > 0 and peltier > 0,
        "is_estimate": True,
    }
