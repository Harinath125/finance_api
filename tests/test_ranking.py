from src.eligibility import check_eligibility
from src.models import Liquidity, Opportunity, RiskLevel, SearchRequest
from src.ranking import rank_opportunities


def make_opp(**overrides) -> Opportunity:
    defaults = dict(
        opportunity_id="OPPX",
        name="Test",
        provider="Provider",
        category="Debt",
        minimum_investment_inr=100_000,
        tenure_min_months=18,
        tenure_max_months=18,
        risk=RiskLevel.MODERATE,
        expected_return_min_pct=9.0,
        expected_return_max_pct=9.0,
        liquidity=Liquidity.HIGH,
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


def make_req(**overrides) -> SearchRequest:
    defaults = dict(investment_amount=200_000, risk=RiskLevel.MODERATE, tenure_months=18)
    defaults.update(overrides)
    return SearchRequest(**defaults)


class TestRankingBasics:
    def test_empty_eligible_list_returns_empty(self):
        assert rank_opportunities(make_req(), []) == []

    def test_scores_are_within_0_100(self):
        req = make_req()
        opps = [make_opp(opportunity_id=f"OPP{i}", minimum_investment_inr=50_000 * i) for i in range(1, 4)]
        eligible = [check_eligibility(req, o) for o in opps]
        eligible = [e for e in eligible if e.eligible]
        ranked = rank_opportunities(req, eligible)
        for r in ranked:
            assert 0 <= r.score <= 100

    def test_sorted_descending_by_score(self):
        req = make_req()
        opps = [
            make_opp(opportunity_id="A", expected_return_max_pct=5.0, expected_return_min_pct=5.0),
            make_opp(opportunity_id="B", expected_return_max_pct=15.0, expected_return_min_pct=15.0),
        ]
        eligible = [e for e in (check_eligibility(req, o) for o in opps) if e.eligible]
        ranked = rank_opportunities(req, eligible)
        scores = [r.score for r in ranked]
        assert scores == sorted(scores, reverse=True)


class TestRiskFitScoring:
    def test_exact_risk_match_scores_higher_than_much_lower_risk(self):
        req = make_req(risk=RiskLevel.HIGH, tenure_months=18)
        high_risk_opp = make_opp(opportunity_id="A", risk=RiskLevel.HIGH)
        low_risk_opp = make_opp(opportunity_id="B", risk=RiskLevel.LOW)
        eligible = [e for e in (check_eligibility(req, o) for o in (high_risk_opp, low_risk_opp)) if e.eligible]
        ranked = {r.eligibility.opportunity.opportunity_id: r.score for r in rank_opportunities(req, eligible)}
        assert ranked["A"] > ranked["B"]


class TestTenureFitScoring:
    def test_tight_window_scores_higher_than_wide_window(self):
        req = make_req(tenure_months=18)
        tight = make_opp(opportunity_id="A", tenure_min_months=18, tenure_max_months=18)
        wide = make_opp(opportunity_id="B", tenure_min_months=6, tenure_max_months=48)
        eligible = [e for e in (check_eligibility(req, o) for o in (tight, wide)) if e.eligible]
        ranked = {r.eligibility.opportunity.opportunity_id: r.score for r in rank_opportunities(req, eligible)}
        assert ranked["A"] > ranked["B"]


class TestBudgetEfficiencyScoring:
    def test_lower_minimum_investment_scores_higher(self):
        req = make_req(investment_amount=500_000)
        cheap = make_opp(opportunity_id="A", minimum_investment_inr=10_000)
        expensive = make_opp(opportunity_id="B", minimum_investment_inr=490_000)
        eligible = [e for e in (check_eligibility(req, o) for o in (cheap, expensive)) if e.eligible]
        ranked = {r.eligibility.opportunity.opportunity_id: r.score for r in rank_opportunities(req, eligible)}
        assert ranked["A"] > ranked["B"]


class TestPreferenceBonusScoring:
    def test_matching_category_scores_higher_than_mismatch(self):
        req = make_req(category="Debt")
        matching = make_opp(opportunity_id="A", category="Debt")
        mismatching = make_opp(opportunity_id="B", category="Equity")
        eligible = [e for e in (check_eligibility(req, o) for o in (matching, mismatching)) if e.eligible]
        ranked = {r.eligibility.opportunity.opportunity_id: r.score for r in rank_opportunities(req, eligible)}
        assert ranked["A"] > ranked["B"]


class TestReasonsAreExplainable:
    def test_every_ranked_result_has_reasons(self):
        req = make_req()
        opp = make_opp()
        eligible = [e for e in [check_eligibility(req, opp)] if e.eligible]
        ranked = rank_opportunities(req, eligible)
        assert len(ranked) == 1
        assert len(ranked[0].reasons) > 0
