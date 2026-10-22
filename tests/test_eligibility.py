from src.eligibility import check_eligibility, find_eligible
from src.models import Liquidity, Opportunity, RiskLevel, SearchRequest


def make_opp(**overrides) -> Opportunity:
    defaults = dict(
        opportunity_id="OPPX",
        name="Test",
        provider="Provider",
        category="Debt",
        minimum_investment_inr=100_000,
        tenure_min_months=12,
        tenure_max_months=24,
        risk=RiskLevel.MODERATE,
        expected_return_min_pct=8.0,
        expected_return_max_pct=10.0,
        liquidity=Liquidity.HIGH,
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


def make_req(**overrides) -> SearchRequest:
    defaults = dict(investment_amount=200_000, risk=RiskLevel.MODERATE, tenure_months=18)
    defaults.update(overrides)
    return SearchRequest(**defaults)


class TestInvestmentAmount:
    def test_amount_below_minimum_is_rejected(self):
        result = check_eligibility(make_req(investment_amount=50_000), make_opp(minimum_investment_inr=100_000))
        assert result.eligible is False
        assert any("minimum" in r for r in result.rejection_reasons)

    def test_amount_equal_to_minimum_is_eligible(self):
        result = check_eligibility(make_req(investment_amount=100_000), make_opp(minimum_investment_inr=100_000))
        assert result.eligible is True

    def test_amount_above_minimum_is_eligible(self):
        result = check_eligibility(make_req(investment_amount=500_000), make_opp(minimum_investment_inr=100_000))
        assert result.eligible is True


class TestRiskTolerance:
    def test_opportunity_risk_above_tolerance_rejected(self):
        result = check_eligibility(make_req(risk=RiskLevel.LOW), make_opp(risk=RiskLevel.HIGH))
        assert result.eligible is False

    def test_opportunity_risk_at_or_below_tolerance_eligible(self):
        result = check_eligibility(make_req(risk=RiskLevel.HIGH), make_opp(risk=RiskLevel.LOW))
        assert result.eligible is True

    def test_exact_risk_match_eligible(self):
        result = check_eligibility(make_req(risk=RiskLevel.MODERATE), make_opp(risk=RiskLevel.MODERATE))
        assert result.eligible is True


class TestTenure:
    def test_tenure_inside_window_eligible(self):
        result = check_eligibility(make_req(tenure_months=18),
                                    make_opp(tenure_min_months=12, tenure_max_months=24))
        assert result.eligible is True

    def test_tenure_below_window_rejected(self):
        result = check_eligibility(make_req(tenure_months=6),
                                    make_opp(tenure_min_months=12, tenure_max_months=24))
        assert result.eligible is False

    def test_tenure_above_window_rejected(self):
        result = check_eligibility(make_req(tenure_months=30),
                                    make_opp(tenure_min_months=12, tenure_max_months=24))
        assert result.eligible is False

    def test_open_ended_window_has_no_upper_bound(self):
        result = check_eligibility(make_req(tenure_months=1000),
                                    make_opp(tenure_min_months=24, tenure_max_months=None))
        assert result.eligible is True

    def test_open_ended_window_still_enforces_minimum(self):
        result = check_eligibility(make_req(tenure_months=1),
                                    make_opp(tenure_min_months=24, tenure_max_months=None))
        assert result.eligible is False


class TestMinimumReturn:
    def test_no_minimum_requested_always_passes(self):
        result = check_eligibility(make_req(minimum_return=None), make_opp(expected_return_max_pct=1.0))
        assert result.eligible is True

    def test_return_meets_minimum_eligible(self):
        result = check_eligibility(make_req(minimum_return=9), make_opp(expected_return_max_pct=10.0))
        assert result.eligible is True

    def test_return_below_minimum_rejected(self):
        result = check_eligibility(make_req(minimum_return=9), make_opp(expected_return_max_pct=5.0))
        assert result.eligible is False

    def test_unsupported_return_with_minimum_requested_is_rejected_not_guessed(self):
        # Opportunity with unknown/"Variable" return can't be proven to meet
        # a stated minimum, so it must not silently pass.
        result = check_eligibility(
            make_req(minimum_return=9),
            make_opp(expected_return_min_pct=None, expected_return_max_pct=None),
        )
        assert result.eligible is False
        assert any("no supported return" in r for r in result.rejection_reasons)


class TestOptionalPreferences:
    def test_category_mismatch_does_not_block_eligibility(self):
        result = check_eligibility(make_req(category="Equity"), make_opp(category="Debt"))
        assert result.eligible is True
        assert result.category_match is False

    def test_category_match_recorded(self):
        result = check_eligibility(make_req(category="Debt"), make_opp(category="Debt"))
        assert result.category_match is True

    def test_no_category_preference_leaves_match_flag_none(self):
        result = check_eligibility(make_req(category=None), make_opp(category="Debt"))
        assert result.category_match is None

    def test_liquidity_mismatch_does_not_block_eligibility(self):
        result = check_eligibility(make_req(liquidity=Liquidity.LOW), make_opp(liquidity=Liquidity.HIGH))
        assert result.eligible is True
        assert result.liquidity_match is False


class TestInvalidAndDuplicateRecords:
    def test_invalid_record_excluded(self):
        result = check_eligibility(make_req(), make_opp(is_valid=False))
        assert result.eligible is False

    def test_duplicate_record_excluded(self):
        result = check_eligibility(make_req(), make_opp(is_duplicate=True))
        assert result.eligible is False


class TestExplicitNoMatch:
    def test_find_eligible_returns_empty_list_when_nothing_matches(self):
        opps = [make_opp(minimum_investment_inr=10_000_000)]  # way above any request
        result = find_eligible(make_req(investment_amount=1000), opps)
        assert result == []

    def test_find_eligible_returns_only_matching(self):
        opps = [
            make_opp(opportunity_id="A", minimum_investment_inr=50_000),
            make_opp(opportunity_id="B", minimum_investment_inr=10_000_000),
        ]
        result = find_eligible(make_req(investment_amount=100_000), opps)
        assert [r.opportunity.opportunity_id for r in result] == ["A"]
