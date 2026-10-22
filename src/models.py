"""
Domain models + API schemas.

Two groups here: the internal dataclasses (Opportunity, ValidationIssue)
that the validator/eligibility/ranking code passes around, and the
Pydantic models (SearchRequest, OpportunityResult, SearchResponse) that
FastAPI uses for request/response validation. Kept separate on purpose -
changing the internal shape shouldn't accidentally change the API contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator
# Pydantic integration is native — SearchRequest / SearchResponse are the request/response
# validation, no separate serializer layer needed like DRF requires
#the /docs interactive UI you've been using is generated automatically from the same type hints, at zero extra cost. Flask would need extra libraries (e.g. Marshmallow, Flask-RESTX) to geteither of those

# ---------------------------------------------------------------------------
# Internal domain model
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    """Risk scale, low to high. Order matters - .rank is used for tolerance checks."""
    LOW = "Low"
    LOW_MODERATE = "Low-Moderate"
    MODERATE = "Moderate"
    MODERATE_HIGH = "Moderate-High"
    HIGH = "High"

    @property
    def rank(self) -> int:
        return _RISK_ORDER[self]


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.LOW_MODERATE: 1,
    RiskLevel.MODERATE: 2,
    RiskLevel.MODERATE_HIGH: 3,
    RiskLevel.HIGH: 4,
}


class Liquidity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


@dataclass
class ValidationIssue:
    """One problem found while validating a raw row."""
    opportunity_id: str
    field: str
    message: str
    severity: str = "error"  # "error" -> field unusable, "warning" -> just a heads-up


@dataclass
class Opportunity:
    """A cleaned-up opportunity record, after the validator has run on it."""
    opportunity_id: str
    name: str
    provider: str
    category: str
    minimum_investment_inr: Optional[int]
    tenure_min_months: Optional[int]
    tenure_max_months: Optional[int]  # None = open-ended, e.g. "24 months+"
    risk: Optional[RiskLevel]
    expected_return_min_pct: Optional[float]
    expected_return_max_pct: Optional[float]
    liquidity: Optional[Liquidity]
    is_duplicate: bool = False
    is_valid: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def expected_return_avg_pct(self) -> Optional[float]:
        """Midpoint of min/max return, or None if we don't have both."""
        if self.expected_return_min_pct is None or self.expected_return_max_pct is None:
            return None
        return (self.expected_return_min_pct + self.expected_return_max_pct) / 2


# ---------------------------------------------------------------------------
# API schemas (Pydantic)
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    investment_amount: float = Field(..., gt=0, description="Amount the user wants to invest, in INR")
    risk: RiskLevel = Field(..., description="Maximum risk tolerance")
    tenure_months: int = Field(..., gt=0, description="User's intended holding period, in months")
    minimum_return: Optional[float] = Field(
        None, ge=0, description="Minimum acceptable expected return, percent p.a."
    )
    category: Optional[str] = Field(None, description="Preferred opportunity category")
    liquidity: Optional[Liquidity] = Field(None, description="Preferred liquidity level")

    @field_validator("risk", mode="before")
    @classmethod
    def _normalize_risk_input(cls, v):
        # Let API callers use the same loose spellings the CSV data does
        # (e.g. "Medium") instead of forcing the exact enum value.
        from src.validator import normalize_risk  # imported here to dodge a circular import
        if isinstance(v, str):
            normalized = normalize_risk(v)
            if normalized is not None:
                return normalized.value
        return v


class OpportunityResult(BaseModel):
    opportunity_id: str
    name: str
    category: str
    score: float
    reasons: list[str]
    minimum_investment_inr: Optional[int]
    tenure_min_months: Optional[int]
    tenure_max_months: Optional[int]
    risk: Optional[str]
    expected_return_min_pct: Optional[float]
    expected_return_max_pct: Optional[float]
    liquidity: Optional[str]


class SearchResponse(BaseModel):
    count: int
    results: list[OpportunityResult]
    validation_warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal data-quality issues detected in the source dataset "
                    "(e.g. duplicate IDs, unsupported return values).",
    )
