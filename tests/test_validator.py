import pytest

from src.models import Liquidity, RiskLevel
from src.validator import (
    normalize_money_to_inr,
    normalize_return,
    normalize_risk,
    normalize_row,
    normalize_tenure_to_months,
    validate_and_normalize,
)


class TestMoneyNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("₹1,00,000", 100_000),
        ("Rs. 2 lakh", 200_000),
        ("25000", 25_000),
        ("₹50,000", 50_000),
        ("₹5 lakh", 500_000),
        ("₹1.5 lakh", 150_000),
        ("₹1.25 lakh", 125_000),
        ("₹10 lakh", 1_000_000),
        ("₹1 crore", 10_000_000),
        ("INR 75000", 75_000),
        ("2 Lakhs", 200_000),   # plural form — not in the source dataset, but should still parse
        ("1 Crore", 10_000_000),
    ])
    def test_parses_known_formats(self, raw, expected):
        assert normalize_money_to_inr(raw) == expected

    def test_empty_string_returns_none(self):
        assert normalize_money_to_inr("") is None

    def test_garbage_returns_none(self):
        assert normalize_money_to_inr("not a number") is None


class TestTenureNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("18 months", (18, 18)),
        ("12-24 months", (12, 24)),
        ("3 years", (36, 36)),
        ("24-30 months", (24, 30)),
        ("24 months+", (24, None)),
        ("36 months+", (36, None)),
        ("9-15 months", (9, 15)),
    ])
    def test_parses_known_formats(self, raw, expected):
        assert normalize_tenure_to_months(raw) == expected

    def test_unparseable_returns_none_none(self):
        assert normalize_tenure_to_months("soon") == (None, None)


class TestRiskNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("Low", RiskLevel.LOW),
        ("Medium", RiskLevel.MODERATE),  # dataset synonym, OPP002
        ("Moderate", RiskLevel.MODERATE),
        ("Moderately High", RiskLevel.MODERATE_HIGH),  # dataset synonym, OPP005
        ("Moderate-High", RiskLevel.MODERATE_HIGH),
        ("High", RiskLevel.HIGH),
    ])
    def test_maps_synonyms(self, raw, expected):
        assert normalize_risk(raw) == expected

    def test_unknown_term_returns_none(self):
        assert normalize_risk("Extremely Risky") is None

    def test_risk_ordering(self):
        assert RiskLevel.LOW.rank < RiskLevel.LOW_MODERATE.rank < RiskLevel.MODERATE.rank
        assert RiskLevel.MODERATE.rank < RiskLevel.MODERATE_HIGH.rank < RiskLevel.HIGH.rank


class TestReturnNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("9.2% p.a.", (9.2, 9.2)),
        ("10.1-11.0% p.a.", (10.1, 11.0)),
        ("15% p.a.", (15.0, 15.0)),
    ])
    def test_parses_known_formats(self, raw, expected):
        assert normalize_return(raw) == expected

    def test_variable_returns_none_none(self):
        # "Variable" is a legitimate value, not a parse error.
        assert normalize_return("Variable") == (None, None)


class TestRowNormalization:
    def _base_row(self, **overrides):
        row = {
            "opportunity_id": "OPPX",
            "name": "Test Opp",
            "provider": "Test Provider",
            "category": "Debt",
            "minimum_investment": "₹1,00,000",
            "tenure": "18 months",
            "risk": "Moderate",
            "expected_return": "9.2% p.a.",
            "liquidity": "High",
        }
        row.update(overrides)
        return row

    def test_well_formed_row_is_valid(self):
        opp = normalize_row(self._base_row())
        assert opp.is_valid
        assert opp.minimum_investment_inr == 100_000
        assert opp.liquidity == Liquidity.HIGH

    def test_missing_required_field_flagged(self):
        opp = normalize_row(self._base_row(provider=""))
        assert any(i.field == "provider" for i in opp.issues)

    def test_unparseable_money_marks_invalid_and_reports(self):
        opp = normalize_row(self._base_row(minimum_investment="a lot of money"))
        assert opp.is_valid is False
        assert any(i.field == "minimum_investment" for i in opp.issues)

    def test_missing_opportunity_id_marks_invalid(self):
        opp = normalize_row(self._base_row(opportunity_id=""))
        assert opp.is_valid is False

    def test_variable_return_is_warning_not_blocking(self):
        opp = normalize_row(self._base_row(expected_return="Variable"))
        # A missing/unsupported return shouldn't block the whole record.
        assert opp.is_valid is True
        assert opp.expected_return_min_pct is None
        assert any(i.severity == "warning" and i.field == "expected_return" for i in opp.issues)


class TestDuplicateDetection:
    def test_second_occurrence_flagged_duplicate(self):
        rows = [
            {
                "opportunity_id": "OPP001", "name": "A", "provider": "P", "category": "Debt",
                "minimum_investment": "10000", "tenure": "12 months", "risk": "Low",
                "expected_return": "6% p.a.", "liquidity": "High",
            },
            {
                "opportunity_id": "OPP001", "name": "A dup", "provider": "P", "category": "Debt",
                "minimum_investment": "10000", "tenure": "12 months", "risk": "Low",
                "expected_return": "6% p.a.", "liquidity": "High",
            },
        ]
        opps, issues = validate_and_normalize(rows)
        assert opps[0].is_duplicate is False
        assert opps[1].is_duplicate is True
        assert any("Duplicate" in i.message for i in issues)

    def test_real_dataset_flags_exactly_one_duplicate(self):
        from src.data_loader import load_opportunities_raw
        rows = load_opportunities_raw("data/opportunities.csv")
        opps, _ = validate_and_normalize(rows)
        duplicates = [o for o in opps if o.is_duplicate]
        assert len(duplicates) == 1
        assert duplicates[0].opportunity_id == "OPP030"

    def test_real_dataset_all_30_unique_plus_one_duplicate_load(self):
        from src.data_loader import load_opportunities_raw
        rows = load_opportunities_raw("data/opportunities.csv")
        assert len(rows) == 31  # 30 unique + 1 intentional duplicate row
        opps, _ = validate_and_normalize(rows)
        assert all(o.is_valid for o in opps)  # every record should be parseable
