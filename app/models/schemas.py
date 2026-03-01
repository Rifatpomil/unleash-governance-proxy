"""Pydantic schemas for API request/response."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# --- Change Request ---


class StrategyInput(BaseModel):
    """Strategy configuration for a feature environment."""

    name: str = Field(..., description="Strategy type name")
    title: Optional[str] = None
    disabled: Optional[bool] = False
    sortOrder: Optional[int] = None
    constraints: Optional[list[dict]] = None
    parameters: Optional[dict] = None
    segments: Optional[list[int]] = None


class ChangeRequestCreate(BaseModel):
    """Request body for creating a change request."""

    project_id: str = Field(default="default", description="Unleash project ID")
    tenant: Optional[str] = Field(default=None, description="Tenant for authz")
    desired_changes: dict = Field(
        ...,
        description="Desired feature changes (description, type, enabled, etc.)",
    )
    environment: Optional[str] = Field(
        default="default",
        description="Target environment",
    )
    strategies: Optional[list[StrategyInput]] = Field(
        default=None,
        description="Strategies to add for the environment",
    )


class ChangeRequestResponse(BaseModel):
    """Change request response."""

    id: str
    flag_key: str
    project_id: str
    tenant: Optional[str]
    status: str
    desired_changes: dict
    environment: Optional[str]
    strategies: Optional[list[dict]]
    created_by: str
    created_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChangeRequestApplyResponse(BaseModel):
    """Response after applying a change request."""

    change_request_id: str
    status: str = "applied"
    unleash_result: Optional[dict] = None
