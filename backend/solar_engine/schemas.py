from pydantic import BaseModel, Field, model_validator


class EstimateRequest(BaseModel):
    monthly_units: float | None = Field(default=None, ge=0)
    monthly_bill: float | None = Field(default=None, ge=0)
    tariff: float | None = Field(default=None, gt=0)
    roof_area_sqft: float | None = Field(default=None, gt=0)
    system_type: str = "ON_GRID"
    subsidy_eligible: bool = False
    requested_size_kw: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def check_inputs(self):
        if self.monthly_units is None and self.monthly_bill is None and self.requested_size_kw is None:
            raise ValueError("Provide monthly_units, monthly_bill or requested_size_kw")
        return self


class EstimateOut(BaseModel):
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
    assumptions: dict
    notes: list[str]
