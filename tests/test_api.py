import pytest
from fastapi.testclient import TestClient

from src.api import app, _load_dataset


@pytest.fixture(scope="module")
def client():
    _load_dataset()  # TestClient doesn't reliably fire startup events across versions; load explicitly
    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["opportunities_loaded"] > 0


class TestSearchEndpointHappyPath:
    def test_search_from_assignment_example_returns_results(self, client):
        payload = {
            "investment_amount": 200000,
            "risk": "Moderate",
            "tenure_months": 24,
            "minimum_return": 9,
            "category": "Debt",
            "liquidity": "Medium",
        }
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert "count" in body and "results" in body
        assert body["count"] == len(body["results"])
        if body["results"]:
            top = body["results"][0]
            assert {"opportunity_id", "name", "score", "reasons"} <= top.keys()
            # results should be sorted by score, descending
            scores = [r["score"] for r in body["results"]]
            assert scores == sorted(scores, reverse=True)

    def test_search_without_optional_fields(self, client):
        payload = {"investment_amount": 50000, "risk": "Low", "tenure_months": 12}
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 200

    def test_response_includes_validation_warnings(self, client):
        payload = {"investment_amount": 50000, "risk": "High", "tenure_months": 12}
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 200
        assert isinstance(resp.json()["validation_warnings"], list)
        assert any("OPP030" in w for w in resp.json()["validation_warnings"])


class TestSearchEndpointNoMatch:
    def test_impossibly_high_minimum_returns_explicit_empty(self, client):
        payload = {"investment_amount": 100, "risk": "Low", "tenure_months": 1}
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
        assert resp.json()["results"] == []


class TestSearchEndpointErrorHandling:
    def test_negative_investment_amount_rejected(self, client):
        payload = {"investment_amount": -500, "risk": "Moderate", "tenure_months": 12}
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 422

    def test_zero_investment_amount_rejected(self, client):
        payload = {"investment_amount": 0, "risk": "Moderate", "tenure_months": 12}
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 422

    def test_invalid_risk_value_rejected(self, client):
        payload = {"investment_amount": 50000, "risk": "Nonsense", "tenure_months": 12}
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 422

    def test_invalid_tenure_rejected(self, client):
        payload = {"investment_amount": 50000, "risk": "Moderate", "tenure_months": -5}
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 422

    def test_missing_required_field_rejected(self, client):
        payload = {"risk": "Moderate", "tenure_months": 12}  # investment_amount missing
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 422

    def test_malformed_json_rejected(self, client):
        resp = client.post(
            "/opportunities/search",
            content="{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_risk_synonym_accepted_on_api_boundary(self, client):
        # "Medium" is the same dataset-observed synonym normalize_risk() maps
        # to Moderate; the API should accept it rather than 422-ing.
        payload = {"investment_amount": 50000, "risk": "Medium", "tenure_months": 12}
        resp = client.post("/opportunities/search", json=payload)
        assert resp.status_code == 200
