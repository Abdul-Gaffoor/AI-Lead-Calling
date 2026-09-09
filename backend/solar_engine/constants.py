"""Engineering constants for the solar calculation engine (MVP section 19).

⚠️ THESE DEFAULTS MUST BE REVIEWED AND SIGNED OFF BY SWARAJ SOLAR
ENGINEERING BEFORE ANY CUSTOMER-FACING USE. They are documented starting
points for Telangana / Andhra Pradesh, not authoritative figures, and every
value here is overridable through settings so the approved numbers can be
applied without a code change.

Subsidy slabs in particular change with government notifications — treat
SUBSIDY_SLABS as a placeholder until the current PM Surya Ghar values are
confirmed from an official source.
"""

#: Average usable peak sun hours per day for the Telangana/AP region.
PEAK_SUN_HOURS = 5.0

#: System performance ratio (losses: inverter, wiring, dust, temperature).
PERFORMANCE_RATIO = 0.75

#: Derived daily yield per installed kWp. PEAK_SUN_HOURS * PERFORMANCE_RATIO.
DAILY_YIELD_PER_KWP = PEAK_SUN_HOURS * PERFORMANCE_RATIO  # 3.75 kWh/kWp/day

#: Roof area needed per kWp, in square feet.
ROOF_SQFT_PER_KWP = 90.0

#: Default domestic tariff (Rs per unit) when the customer has not told us.
DEFAULT_TARIFF = 8.0

#: Indicative installed cost per kWp (Rs), by system size band.
#: (max_kw_exclusive, low_rate, high_rate)
COST_BANDS: list[tuple[float, float, float]] = [
    (3.0, 55000.0, 65000.0),
    (10.0, 48000.0, 58000.0),
    (100.0, 42000.0, 52000.0),
    (float("inf"), 38000.0, 46000.0),
]

#: Cost multiplier by system type.
SYSTEM_TYPE_MULTIPLIER = {
    "ON_GRID": 1.0,
    "HYBRID": 1.35,
    "OFF_GRID": 1.5,
}

#: PM Surya Ghar residential subsidy slabs — (up_to_kw, rupees_per_kw).
#: PLACEHOLDER: confirm against the current official notification.
SUBSIDY_SLABS: list[tuple[float, float]] = [
    (2.0, 30000.0),
    (3.0, 18000.0),
]

#: Maximum residential subsidy payable (Rs). PLACEHOLDER — confirm.
SUBSIDY_CAP = 78000.0

#: Sizes we actually install, in kWp. Estimates snap to these.
STANDARD_SIZES = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0, 25.0, 50.0, 100.0]
