from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ItemWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal = Field(ge=0, le=1000000, max_digits=12, decimal_places=2, allow_inf_nan=False)
    is_active: bool = True


class ItemRead(ItemWrite):
    model_config = ConfigDict(from_attributes=True, extra="forbid", str_strip_whitespace=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


class ItemPage(BaseModel):
    items: list[ItemRead]
    total: int
    limit: int
    offset: int


class DependencyStatus(BaseModel):
    postgres: str
    redis: str


class Readiness(BaseModel):
    status: str
    dependencies: DependencyStatus
