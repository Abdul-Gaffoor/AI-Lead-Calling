import pytest

from backend.solar_engine import constants as C
from backend.solar_engine.calculator import estimate, residential_subsidy


def test_sizing_from_units():
    # 620 units/month -> ~20.7 units/day -> ~5.5 kWp -> snaps to 6 kW
    result = estimate(monthly_units=620)
    assert result.system_size_kw == 6.0
    assert result.roof_area_required_sqft == 6.0 * C.ROOF_SQFT_PER_KWP
    assert result.annual_generation_kwh == pytest.approx(6.0 * C.DAILY_YIELD_PER_KWP * 365, rel=0.01)


def test_bill_is_converted_to_units():
    from_units = estimate(monthly_units=7500 / 8.0, tariff=8.0)
    from_bill = estimate(monthly_bill=7500, tariff=8.0)
    assert from_bill.system_size_kw == from_units.system_size_kw
    assert any("estimated from the bill" in note for note in from_bill.notes)


def test_savings_never_exceed_consumption():
    """A 5 kW system on a tiny bill must not claim to save more than is spent."""
    result = estimate(monthly_units=100, requested_size_kw=5.0, tariff=8.0)
    assert result.monthly_savings <= 100 * 8.0


def test_roof_area_caps_system_size():
    unconstrained = estimate(monthly_units=1200)
    constrained = estimate(monthly_units=1200, roof_area_sqft=200)
    assert constrained.system_size_kw < unconstrained.system_size_kw
    assert any("roof area" in note for note in constrained.notes)


def test_roof_too_small_is_rejected():
    with pytest.raises(ValueError, match="roof area is too small"):
        estimate(monthly_units=600, roof_area_sqft=40)


def test_subsidy_slabs_and_cap():
    assert residential_subsidy(1.0) == 30000
    assert residential_subsidy(2.0) == 60000
    assert residential_subsidy(3.0) == 78000
    # Capped beyond 3 kW
    assert residential_subsidy(10.0) == C.SUBSIDY_CAP


def test_subsidy_only_applies_when_eligible():
    without = estimate(monthly_units=300, subsidy_eligible=False)
    with_subsidy = estimate(monthly_units=300, subsidy_eligible=True)
    assert without.subsidy_amount == 0
    assert with_subsidy.subsidy_amount > 0
    assert with_subsidy.net_investment_low < without.net_investment_low


def test_offgrid_is_dearer_and_gets_no_subsidy():
    on_grid = estimate(monthly_units=400, system_type="ON_GRID")
    off_grid = estimate(monthly_units=400, system_type="OFF_GRID", subsidy_eligible=True)
    assert off_grid.project_cost_low > on_grid.project_cost_low
    assert off_grid.subsidy_amount == 0


def test_payback_is_reported():
    result = estimate(monthly_units=620, subsidy_eligible=True)
    assert result.payback_years is not None
    assert 0 < result.payback_years < 30


def test_inputs_are_required():
    with pytest.raises(ValueError):
        estimate()
    with pytest.raises(ValueError):
        estimate(monthly_units=600, tariff=0)


def test_assumptions_are_disclosed():
    """Anyone quoting a customer must be able to see what was assumed."""
    result = estimate(monthly_units=620)
    assert result.assumptions["peak_sun_hours"] == C.PEAK_SUN_HOURS
    assert result.assumptions["performance_ratio"] == C.PERFORMANCE_RATIO
    assert any("site survey" in note for note in result.notes)
