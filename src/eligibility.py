"""
Eligibility checks.

For a given search request, works out which opportunities the user
qualifies for and why (or why not) - every rejection comes with a
readable reason instead of just filtering silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.models import Opportunity, SearchRequest


@dataclass
class EligibilityResult:
    opportunity: Opportunity
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    # None = user didn't state a preference. True/False = they did, and it
    # did/didn't match. Doesn't affect `eligible` - ranking uses it for scoring.
    category_match: Optional[bool] = None
    liquidity_match: Optional[bool] = None


def _tenure_compatible(req: SearchRequest, opp: Opportunity) -> tuple[bool, str]:
    """Requested tenure has to fall inside the opportunity's [min, max] window.
    max=None means open-ended (e.g. "24 months+"), so no upper bound."""
    user_t = req.tenure_months
    opp_min, opp_max = opp.tenure_min_months, opp.tenure_max_months

    if opp_min is not None and user_t < opp_min:
        return False, f"Requested tenure ({user_t}mo) is below the opportunity's minimum ({opp_min}mo)"
    if opp_max is not None and user_t > opp_max:
        return False, f"Requested tenure ({user_t}mo) exceeds the opportunity's maximum ({opp_max}mo)"
    return True, f"Tenure fits within {opp_min}-{opp_max if opp_max is not None else 'open'} months"


def _return_compatible(req: SearchRequest, opp: Opportunity) -> tuple[bool, str]:
    """
    If the user gave a minimum return, only compare against opportunities
    with a known return figure - "Variable"/unparsed returns don't get
    guessed into either bucket, they're just excluded.
    """
    if req.minimum_return is None:
        return True, "No minimum return requested"

    if opp.expected_return_max_pct is None:
        return False, "Opportunity has no supported return figure to compare against the requested minimum"

    # use the best-case (max) figure here - if even the ceiling misses the
    # bar it's a clean reject; if it clears the bar we call it eligible and
    # let ranking dock it later if the *average* return is weaker
    if opp.expected_return_max_pct < req.minimum_return:
        return False, (f"Opportunity's expected return (up to {opp.expected_return_max_pct}%) "
                        f"is below the requested minimum ({req.minimum_return}%)")
    return True, f"Expected return (up to {opp.expected_return_max_pct}%) meets requested minimum"


def check_eligibility(req: SearchRequest, opp: Opportunity) -> EligibilityResult:
    """Evaluate a single opportunity against the user's request."""
    reasons: list[str] = []
    rejections: list[str] = []

    # invalid/duplicate rows never make it into results
    if not opp.is_valid:
        return EligibilityResult(opp, eligible=False,
                                  rejection_reasons=["Source record failed validation"])
    if opp.is_duplicate:
        return EligibilityResult(opp, eligible=False,
                                  rejection_reasons=["Duplicate opportunity_id (excluded)"])

    # investment amount vs. opportunity's minimum
    if opp.minimum_investment_inr is not None and req.investment_amount < opp.minimum_investment_inr:
        rejections.append(
            f"Investment amount (₹{req.investment_amount:,.0f}) is below the opportunity's "
            f"minimum (₹{opp.minimum_investment_inr:,.0f})"
        )
    else:
        reasons.append("Investment amount meets minimum")

    # risk can't exceed the user's tolerance
    if opp.risk is not None:
        if opp.risk.rank > req.risk.rank:
            rejections.append(f"Opportunity risk ({opp.risk.value}) exceeds tolerance ({req.risk.value})")
        else:
            reasons.append(f"Risk ({opp.risk.value}) within tolerance ({req.risk.value})")

    tenure_ok, tenure_msg = _tenure_compatible(req, opp)
    (reasons if tenure_ok else rejections).append(tenure_msg)

    return_ok, return_msg = _return_compatible(req, opp)
    (reasons if return_ok else rejections).append(return_msg)

    # category/liquidity are soft preferences, not hard filters - a mismatch
    # shouldn't remove an otherwise-eligible opportunity, just rank it lower,
    # so we record the match/mismatch and let ranking.py handle the scoring
    category_match: Optional[bool] = None
    if req.category:
        category_match = opp.category.strip().lower() == req.category.strip().lower()
        reasons.append(
            f"Category {'matches' if category_match else 'differs from'} preference ({req.category})"
        )

    liquidity_match: Optional[bool] = None
    if req.liquidity is not None:
        liquidity_match = opp.liquidity == req.liquidity
        reasons.append(
            f"Liquidity {'matches' if liquidity_match else 'differs from'} preference ({req.liquidity.value})"
        )

    eligible = len(rejections) == 0
    return EligibilityResult(
        opp, eligible=eligible, reasons=reasons, rejection_reasons=rejections,
        category_match=category_match, liquidity_match=liquidity_match,
    )


def find_eligible(req: SearchRequest, opportunities: list[Opportunity]) -> list[EligibilityResult]:
    """Run every opportunity through check_eligibility and keep the ones that pass."""
    all_results = [check_eligibility(req, opp) for opp in opportunities]
    return [r for r in all_results if r.eligible]
