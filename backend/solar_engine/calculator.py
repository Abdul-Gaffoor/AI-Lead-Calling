"""Central solar sizing and ROI engine (MVP section 19).

Every consumer — the website calculator, the AI voice agent and sales
executives — goes through this one implementation, so a customer is never
quoted two different numbers. The LLM must never compute these itself.
"""

from dataclasses import dataclass, field

from backend.solar_engine import constants as C


@dataclass
class SolarEstimate:
    system_size_kw: float
    roof_area_required_sqft: float
    monthly_units_offset: float
    annual_generation_kwh: float
    monthly_savings: float
    annual_savings: float
    project_cost_low: float
    project_cost_high: float
    subsidy_amount: float
    net_investment_low: float
    net_investment_high: float
    payback_years: float | None
    assumptions: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _snap_to_standard_size(size_kw: float) -> float:
    """Round up to a size Swaraj actually installs."""
    for standard in C.STANDARD_SIZES:
        if size_kw <= standard:
            return standard
    # Above the largest standard size, round to the nearest 5 kW.
    return round(size_kw / 5.0) * 5.0


def _cost_range(size_kw: float, system_type: str) -> tuple[float, float]:
    multiplier = C.SYSTEM_TYPE_MULTIPLIER.get(system_type.upper(), 1.0)
    for max_kw, low, high in C.COST_BANDS:
        if size_kw < max_kw:
            return size_kw * low * multiplier, size_kw * high * multiplier
    raise AssertionError("COST_BANDS must end with an unbounded band")


def residential_subsidy(size_kw: float) -> float:
    """Subsidy for a residential system.

    PLACEHOLDER SLABS — see constants. Only residential on-grid systems are
    eligible; the caller decides eligibility.
    """
    subsidy = 0.0
    previous_cap = 0.0
    for up_to_kw, rate in C.SUBSIDY_SLABS:
        eligible_kw = max(0.0, min(size_kw, up_to_kw) - previous_cap)
        subsidy += eligible_kw * rate
        previous_cap = up_to_kw
        if size_kw <= up_to_kw:
            break
    return min(subsidy, C.SUBSIDY_CAP)


def estimate(
    *,
    monthly_units: float | None = None,
    monthly_bill: float | None = None,
    tariff: float | None = None,
    roof_area_sqft: float | None = None,
    system_type: str = "ON_GRID",
    subsidy_eligible: bool = False,
    requested_size_kw: float | None = None,
) -> SolarEstimate:
    """Produce an indicative estimate.

    Give either monthly_units or monthly_bill (units are more accurate), or
    a requested_size_kw to price a specific system.
    """
    # An explicitly supplied zero/negative tariff is an error, not a request
    # for the default.
    tariff = C.DEFAULT_TARIFF if tariff is None else tariff
    if tariff <= 0:
        raise ValueError("tariff must be greater than zero")

    notes: list[str] = []

    if monthly_units is None and monthly_bill is None and requested_size_kw is None:
        raise ValueError("Provide monthly_units, monthly_bill or requested_size_kw")

    if monthly_units is None and monthly_bill is not None:
        monthly_units = monthly_bill / tariff
        notes.append("Units estimated from the bill; actual units give a better estimate.")

    if requested_size_kw is not None:
        size_kw = _snap_to_standard_size(requested_size_kw)
    else:
        daily_units = (monthly_units or 0.0) / 30.0
        raw_size = daily_units / C.DAILY_YIELD_PER_KWP
        size_kw = _snap_to_standard_size(raw_size)

    if size_kw <= 0:
        raise ValueError("Consumption is too low to size a system")

    # A roof the customer actually has may cap the system.
    if roof_area_sqft is not None:
        max_by_roof = roof_area_sqft / C.ROOF_SQFT_PER_KWP
        if max_by_roof < size_kw:
            size_kw = _snap_to_standard_size(max_by_roof) if max_by_roof >= 1 else 0.0
            if size_kw == 0.0:
                raise ValueError("Available roof area is too small for a system")
            notes.append("System size limited by the available roof area.")

    annual_generation = size_kw * C.DAILY_YIELD_PER_KWP * 365
    monthly_generation = annual_generation / 12

    # Savings are capped by what the customer actually consumes.
    offset_units = min(monthly_generation, monthly_units) if monthly_units else monthly_generation
    monthly_savings = offset_units * tariff

    cost_low, cost_high = _cost_range(size_kw, system_type)
    subsidy = residential_subsidy(size_kw) if subsidy_eligible else 0.0
    if subsidy_eligible and system_type.upper() != "ON_GRID":
        subsidy = 0.0
        notes.append("Subsidy shown as zero: typically only on-grid systems qualify.")

    net_low = max(0.0, cost_low - subsidy)
    net_high = max(0.0, cost_high - subsidy)

    annual_savings = monthly_savings * 12
    payback = round(((net_low + net_high) / 2) / annual_savings, 1) if annual_savings > 0 else None

    notes.append(
        "Indicative only. Final sizing and pricing are confirmed after a site survey."
    )
    if subsidy_eligible:
        notes.append("Subsidy figures must be confirmed against the current scheme.")

    return SolarEstimate(
        system_size_kw=round(size_kw, 2),
        roof_area_required_sqft=round(size_kw * C.ROOF_SQFT_PER_KWP, 0),
        monthly_units_offset=round(offset_units, 0),
        annual_generation_kwh=round(annual_generation, 0),
        monthly_savings=round(monthly_savings, 0),
        annual_savings=round(annual_savings, 0),
        project_cost_low=round(cost_low, 0),
        project_cost_high=round(cost_high, 0),
        subsidy_amount=round(subsidy, 0),
        net_investment_low=round(net_low, 0),
        net_investment_high=round(net_high, 0),
        payback_years=payback,
        assumptions={
            "tariff": tariff,
            "peak_sun_hours": C.PEAK_SUN_HOURS,
            "performance_ratio": C.PERFORMANCE_RATIO,
            "daily_yield_per_kwp": C.DAILY_YIELD_PER_KWP,
            "roof_sqft_per_kwp": C.ROOF_SQFT_PER_KWP,
            "system_type": system_type.upper(),
        },
        notes=notes,
    )
