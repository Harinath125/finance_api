"""
Ranking engine - scores each eligible opportunity 0-100 and sorts them.

The score is a weighted sum of 5 components (weights below, also in
TECHNICAL_NOTE.md). Each component comes with its own reason string so
the final score isn't just a number with no explanation attached.

    risk_fit          30%  - how close the opp's risk is to the user's max
                              tolerance (using more of the risk budget = better)
    tenure_fit        25%  - how tight the opp's tenure window is around
                              the requested tenure
    return_score      25%  - avg expected return, normalized against the
                              best return in the eligible set
    budget_efficiency 10%  - how little of the stated investment amount
                              the opp's minimum eats up
    preference_bonus  10%  - category/liquidity preference matches, if any
                              were stated
"""

from __future__ import annotations

from dataclasses import dataclass

from src.eligibility import EligibilityResult
from src.models import RiskLevel, SearchRequest

WEIGHTS = {
    "risk_fit": 0.30,
    "tenure_fit": 0.25,
    "return_score": 0.25,
    "budget_efficiency": 0.10,
    "preference_bonus": 0.10,
}

_MAX_RISK_RANK = max(r.rank for r in RiskLevel)


@dataclass
class RankedResult:
    eligibility: EligibilityResult
    score: float  # 0-100
    reasons: list[str]  # eligibility reasons + scoring rationale, in order


_OPEN_ENDED_TENURE_WIDTH = 36  # can't score infinite width as "tight", so cap it here


def _risk_fit(req: SearchRequest, opp_risk: RiskLevel | None) -> tuple[float, str]:
    if opp_risk is None:
        return 0.5, "Risk fit: unknown risk, neutral score"
    gap = req.risk.rank - opp_risk.rank  # >= 0, eligibility already excludes opp_risk > req.risk
    fit = 1.0 - (gap / _MAX_RISK_RANK if _MAX_RISK_RANK else 0)
    return fit, f"Risk fit: {opp_risk.value} vs tolerance {req.risk.value} ({fit:.0%} of budget used)"


def _tenure_fit(req: SearchRequest, tmin: int | None, tmax: int | None) -> tuple[float, str]:
    if tmin is None:
        return 0.5, "Tenure fit: unparsed tenure, neutral score"
    # narrower window = more confident fit, so score decays as it widens
    width = _OPEN_ENDED_TENURE_WIDTH if tmax is None else max(tmax - tmin, 0)
    fit = 1.0 / (1.0 + width / 12.0)  # width=0 -> 1.0, width=12 -> 0.5, width=36 -> 0.25
    return fit, f"Tenure fit: request {req.tenure_months}mo inside window " \
                f"{tmin}-{tmax if tmax is not None else 'open'}mo"


def _return_score(avg_return: float | None, best_avg_return_in_set: float) -> tuple[float, str]:
    if avg_return is None:
        return 0.5, "Return score: unsupported/unknown return, neutral score"
    if best_avg_return_in_set <= 0:
        return 0.5, "Return score: no comparable positive returns in result set, neutral score"
    normalized = min(avg_return / best_avg_return_in_set, 1.0)
    return normalized, f"Return score: {avg_return:.1f}% avg (best in results: {best_avg_return_in_set:.1f}%)"


def _budget_efficiency(investment_amount: float, min_investment: int | None) -> tuple[float, str]:
    if min_investment is None or min_investment <= 0:
        return 0.5, "Budget efficiency: unknown minimum, neutral score"
    ratio = min_investment / investment_amount
    efficiency = max(0.0, min(1.0 - ratio, 1.0))
    return efficiency, f"Budget efficiency: minimum uses {ratio:.0%} of stated investment amount"


def _preference_bonus(category_match: bool | None, liquidity_match: bool | None) -> tuple[float, str]:
    stated = [m for m in (category_match, liquidity_match) if m is not None]
    if not stated:
        return 0.5, "Preference bonus: no optional preferences stated, neutral score"
    matched = sum(1 for m in stated if m)
    fraction = matched / len(stated)
    return fraction, f"Preference bonus: {matched}/{len(stated)} optional preference(s) matched"


def rank_opportunities(req: SearchRequest, eligible: list[EligibilityResult]) -> list[RankedResult]:
    """Score every eligible result and sort highest-first (ties broken by opportunity_id)."""
    if not eligible:
        return []

    avg_returns = [r.opportunity.expected_return_avg_pct for r in eligible]
    known_avg_returns = [v for v in avg_returns if v is not None]
    best_avg_return = max(known_avg_returns) if known_avg_returns else 0.0

    ranked: list[RankedResult] = []
    for r in eligible:
        opp = r.opportunity
        risk_score, risk_reason = _risk_fit(req, opp.risk)
        tenure_score, tenure_reason = _tenure_fit(req, opp.tenure_min_months, opp.tenure_max_months)
        return_score, return_reason = _return_score(opp.expected_return_avg_pct, best_avg_return)
        budget_score, budget_reason = _budget_efficiency(req.investment_amount, opp.minimum_investment_inr)
        pref_score, pref_reason = _preference_bonus(r.category_match, r.liquidity_match)

        total = 100 * (
            WEIGHTS["risk_fit"] * risk_score
            + WEIGHTS["tenure_fit"] * tenure_score
            + WEIGHTS["return_score"] * return_score
            + WEIGHTS["budget_efficiency"] * budget_score
            + WEIGHTS["preference_bonus"] * pref_score
        )

        all_reasons = list(r.reasons) + [risk_reason, tenure_reason, return_reason, budget_reason, pref_reason]
        ranked.append(RankedResult(eligibility=r, score=round(total, 2), reasons=all_reasons))

    ranked.sort(key=lambda rr: (-rr.score, rr.eligibility.opportunity.opportunity_id))
    return ranked
