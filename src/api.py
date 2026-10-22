"""
REST API layer - HTTP stuff only (routing, status codes, request/response
marshalling). The actual logic lives in validator.py / eligibility.py /
ranking.py so it can be tested without spinning up FastAPI at all.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.data_loader import DataLoadError, load_opportunities_raw
from src.eligibility import find_eligible
from src.models import Opportunity, OpportunityResult, SearchRequest, SearchResponse, ValidationIssue
from src.ranking import rank_opportunities
from src.validator import validate_and_normalize

logger = logging.getLogger("ki_wealth_tech.api")

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "opportunities.csv"

# loaded once at startup and kept in memory - fine for a dataset this size,
# a real version would need a reload endpoint or a scheduled refresh
_opportunities: list[Opportunity] = []
_dataset_issues: list[ValidationIssue] = []


def _load_dataset() -> None:
    global _opportunities, _dataset_issues
    try:
        raw_rows = load_opportunities_raw(DATA_PATH)
        _opportunities, _dataset_issues = validate_and_normalize(raw_rows)
        logger.info(
            "Loaded %d opportunities (%d validation issues, %d duplicates)",
            len(_opportunities), len(_dataset_issues),
            sum(1 for o in _opportunities if o.is_duplicate),
        )
    except DataLoadError as exc:
        # better to fail at startup than silently serve an empty API
        logger.error("Failed to load dataset: %s", exc)
        raise


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _load_dataset()
    yield


app = FastAPI(
    title="K&I Wealth Tech — Financial Opportunity Processing & Eligibility API",
    version="1.0.0",
    description="Ingests opportunity data, applies eligibility rules, and ranks matches.",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": exc.errors()})


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error while processing %s", request.url)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "opportunities_loaded": len(_opportunities)}


@app.post("/opportunities/search", response_model=SearchResponse)
def search_opportunities(request: SearchRequest) -> SearchResponse:
    """
    Find and rank opportunities matching the caller's criteria.

    Returns count=0/results=[] rather than an error when nothing matches -
    no matches is a valid outcome, not a failure.
    """
    eligible = find_eligible(request, _opportunities)
    ranked = rank_opportunities(request, eligible)

    results = [
        OpportunityResult(
            opportunity_id=r.eligibility.opportunity.opportunity_id,
            name=r.eligibility.opportunity.name,
            category=r.eligibility.opportunity.category,
            score=r.score,
            reasons=r.reasons,
            minimum_investment_inr=r.eligibility.opportunity.minimum_investment_inr,
            tenure_min_months=r.eligibility.opportunity.tenure_min_months,
            tenure_max_months=r.eligibility.opportunity.tenure_max_months,
            risk=r.eligibility.opportunity.risk.value if r.eligibility.opportunity.risk else None,
            expected_return_min_pct=r.eligibility.opportunity.expected_return_min_pct,
            expected_return_max_pct=r.eligibility.opportunity.expected_return_max_pct,
            liquidity=r.eligibility.opportunity.liquidity.value if r.eligibility.opportunity.liquidity else None,
        )
        for r in ranked
    ]

    warnings = [f"[{i.severity}] {i.opportunity_id}/{i.field}: {i.message}" for i in _dataset_issues]

    return SearchResponse(count=len(results), results=results, validation_warnings=warnings)
