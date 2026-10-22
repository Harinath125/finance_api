"""
Cleans up the raw CSV rows into proper Opportunity objects.

Bad rows aren't dropped - each problem gets recorded as a ValidationIssue
so it shows up in the API's warnings instead of just disappearing.

The normalize_* functions below are all pure (take a string, return a
parsed value or None) so they're easy to test without touching a file.
"""

from __future__ import annotations

import re
from typing import Optional

from src.models import Liquidity, Opportunity, RiskLevel, ValidationIssue

REQUIRED_FIELDS = [
    "opportunity_id",
    "name",
    "provider",
    "category",
    "minimum_investment",
    "tenure",
    "risk",
    "expected_return",
    "liquidity",
]

# ---------------------------------------------------------------------------
# Monetary normalization
# ---------------------------------------------------------------------------

_LAKH = 100_000
_CRORE = 10_000_000

_MONEY_UNIT_RE = re.compile(
    r"(?P<num>[\d,]+(?:\.\d+)?)\s*(?P<unit>lakhs?|lacs?|crores?|cr)?", re.IGNORECASE
)


def normalize_money_to_inr(raw: str) -> Optional[int]:
    """
    Turn a free-text INR amount into a plain int (rupees).

      "₹1,00,000"   -> 100000
      "Rs. 2 lakh"  -> 200000
      "25000"       -> 25000
      "₹1.25 lakh"  -> 125000
      "₹1 crore"    -> 10000000

    Returns None if it can't be parsed.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    # drop currency symbol/prefix, then the Indian-style grouping commas
    text = re.sub(r"(₹|rs\.?|inr)", "", text, flags=re.IGNORECASE).strip()
    text = text.replace(",", "")

    match = _MONEY_UNIT_RE.fullmatch(text)
    if not match or not match.group("num"):
        return None

    try:
        value = float(match.group("num"))
    except ValueError:
        return None

    unit = (match.group("unit") or "").lower()
    if unit in ("lakh", "lakhs", "lac", "lacs"):#added lakhs also
        value *= _LAKH
    elif unit in ("crore", "crores", "cr"):
        value *= _CRORE

    return int(round(value))


# ---------------------------------------------------------------------------
# Tenure normalization
# ---------------------------------------------------------------------------

_TENURE_RE = re.compile(
    # unit uses "s?" not "es?" - without it "months" could short-match as
    # "month" and leave a stray "s" sitting before the "+", which broke
    # matching on "24 months+"
    r"(?P<min>\d+)\s*(?:-\s*(?P<max>\d+))?\s*(?P<unit>years?|yrs?|months?)\s*(?P<plus>\+)?",
    re.IGNORECASE,
)


def normalize_tenure_to_months(raw: str) -> tuple[Optional[int], Optional[int]]:
    """
    Parse free-text tenure into (min_months, max_months).

      "18 months"    -> (18, 18)
      "12-24 months" -> (12, 24)
      "3 years"      -> (36, 36)
      "24 months+"   -> (24, None)   open-ended, i.e. "at least 24 months"

    Returns (None, None) if it can't be parsed.
    """
    if raw is None:
        return None, None
    text = raw.strip()
    if not text:
        return None, None

    match = _TENURE_RE.search(text)
    if not match:
        return None, None

    unit = match.group("unit").lower()
    multiplier = 12 if unit.startswith("y") else 1

    tmin = int(match.group("min")) * multiplier
    tmax_raw = match.group("max")
    is_open_ended = bool(match.group("plus"))

    if is_open_ended:
        return tmin, None
    if tmax_raw is not None:
        return tmin, int(tmax_raw) * multiplier
    return tmin, tmin


# ---------------------------------------------------------------------------
# Risk normalization
# ---------------------------------------------------------------------------

_RISK_SYNONYMS = {
    "low": RiskLevel.LOW,
    "low-moderate": RiskLevel.LOW_MODERATE,
    "low moderate": RiskLevel.LOW_MODERATE,
    "moderate": RiskLevel.MODERATE,
    "medium": RiskLevel.MODERATE,  # dataset uses "Medium" as a synonym (OPP002)
    "moderate-high": RiskLevel.MODERATE_HIGH,
    "moderately high": RiskLevel.MODERATE_HIGH,  # dataset spelling (OPP005)
    "moderate high": RiskLevel.MODERATE_HIGH,
    "high-moderate": RiskLevel.MODERATE_HIGH,
    "high": RiskLevel.HIGH,
}


def normalize_risk(raw: str) -> Optional[RiskLevel]:
    """Map the dataset's risk wording onto the 5-point RiskLevel scale."""
    if raw is None:
        return None
    key = raw.strip().lower()
    return _RISK_SYNONYMS.get(key)


# ---------------------------------------------------------------------------
# Return normalization
# ---------------------------------------------------------------------------

_RETURN_RANGE_RE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)\s*(?:-\s*(?P<max>\d+(?:\.\d+)?))?\s*%"
)


def normalize_return(raw: str) -> tuple[Optional[float], Optional[float]]:
    """
    Parse free-text expected return into (min_pct, max_pct).

      "9.2% p.a."        -> (9.2, 9.2)
      "10.1-11.0% p.a."  -> (10.1, 11.0)
      "Variable"         -> (None, None)
    """
    if raw is None:
        return None, None
    text = raw.strip()
    if not text:
        return None, None

    match = _RETURN_RANGE_RE.search(text)
    if not match:
        # "Variable" lands here too - that's fine, it just means there's no
        # fixed number to compare against later, not a broken row.
        return None, None

    rmin = float(match.group("min"))
    rmax = float(match.group("max")) if match.group("max") is not None else rmin
    return rmin, rmax


# ---------------------------------------------------------------------------
# Row-level validation + normalization
# ---------------------------------------------------------------------------

def _check_missing_fields(row: dict, opp_id: str) -> list[ValidationIssue]:
    issues = []
    for field_name in REQUIRED_FIELDS:
        value = (row.get(field_name) or "").strip()
        if not value:
            issues.append(
                ValidationIssue(
                    opportunity_id=opp_id or "<unknown>",
                    field=field_name,
                    message=f"Missing required field '{field_name}'",
                    severity="error",
                )
            )
    return issues


def normalize_row(row: dict) -> Opportunity:
    """
    Turn one raw CSV row into an Opportunity.

    Doesn't raise on bad data - a field that won't parse just becomes None
    and gets a ValidationIssue attached, so the row still shows up (marked
    invalid) instead of silently vanishing.
    """
    opp_id = (row.get("opportunity_id") or "").strip()
    issues = _check_missing_fields(row, opp_id)

    money = normalize_money_to_inr(row.get("minimum_investment", ""))
    if money is None and (row.get("minimum_investment") or "").strip():
        issues.append(ValidationIssue(opp_id, "minimum_investment",
                                       f"Unparseable amount: {row.get('minimum_investment')!r}"))

    tmin, tmax = normalize_tenure_to_months(row.get("tenure", ""))
    if tmin is None and (row.get("tenure") or "").strip():
        issues.append(ValidationIssue(opp_id, "tenure",
                                       f"Unparseable tenure: {row.get('tenure')!r}"))

    risk = normalize_risk(row.get("risk", ""))
    if risk is None and (row.get("risk") or "").strip():
        issues.append(ValidationIssue(opp_id, "risk",
                                       f"Unrecognized risk term: {row.get('risk')!r}"))

    rmin, rmax = normalize_return(row.get("expected_return", ""))
    if rmin is None:
        raw_return = (row.get("expected_return") or "").strip()
        if raw_return and raw_return.lower() != "variable":
            issues.append(ValidationIssue(opp_id, "expected_return",
                                           f"Unparseable return: {raw_return!r}"))
        elif raw_return.lower() == "variable":
            issues.append(ValidationIssue(
                opp_id, "expected_return",
                "Return is 'Variable' — no fixed figure supplied by provider",
                severity="warning",
            ))

    liquidity_raw = (row.get("liquidity") or "").strip()
    try:
        liquidity = Liquidity(liquidity_raw) if liquidity_raw else None
    except ValueError:
        liquidity = None
        issues.append(ValidationIssue(opp_id, "liquidity",
                                       f"Unrecognized liquidity value: {liquidity_raw!r}"))

    # only block a record if the fields eligibility/ranking actually depend
    # on (id, min investment, tenure, risk) failed to parse
    has_blocking_error = (
        not opp_id
        or money is None
        or tmin is None
        or risk is None
    )

    return Opportunity(
        opportunity_id=opp_id,
        name=(row.get("name") or "").strip(),
        provider=(row.get("provider") or "").strip(),
        category=(row.get("category") or "").strip(),
        minimum_investment_inr=money,
        tenure_min_months=tmin,
        tenure_max_months=tmax,
        risk=risk,
        expected_return_min_pct=rmin,
        expected_return_max_pct=rmax,
        liquidity=liquidity,
        is_valid=not has_blocking_error,
        issues=issues,
    )


def validate_and_normalize(raw_rows: list[dict]) -> tuple[list[Opportunity], list[ValidationIssue]]:
    """
    Normalize every row and flag duplicate opportunity_ids.

    Duplicates aren't removed here, just marked - the first occurrence is
    kept, later ones get is_duplicate=True so eligibility/ranking skip them
    but the warning still shows up in the report.
    """
    opportunities = [normalize_row(row) for row in raw_rows]
    all_issues: list[ValidationIssue] = []

    seen_ids: dict[str, int] = {}
    for idx, opp in enumerate(opportunities):
        if not opp.opportunity_id:
            continue
        if opp.opportunity_id in seen_ids:
            opp.is_duplicate = True
            dup_issue = ValidationIssue(
                opportunity_id=opp.opportunity_id,
                field="opportunity_id",
                message=f"Duplicate opportunity_id (first seen at row {seen_ids[opp.opportunity_id] + 1}); "
                        f"this later occurrence is excluded from results",
                severity="warning",
            )
            opp.issues.append(dup_issue)
        else:
            seen_ids[opp.opportunity_id] = idx

    for opp in opportunities:
        all_issues.extend(opp.issues)

    return opportunities, all_issues
