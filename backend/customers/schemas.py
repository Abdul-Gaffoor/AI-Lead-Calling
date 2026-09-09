import datetime as dt

from pydantic import BaseModel, ConfigDict

from backend.leads.schemas import LeadOut


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone: str
    name: str
    opted_out: bool
    opted_out_at: dt.datetime | None
    is_existing_customer: bool
    created_at: dt.datetime


class CustomerDetail(CustomerOut):
    leads: list[LeadOut] = []
