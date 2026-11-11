from types import SimpleNamespace

import pytest

from app.main import Candidate, Outcome, calculate, calculate_position_roi, net_decimal_odds


def test_betfair_commission_is_applied_to_winnings_only():
    assert net_decimal_odds(2.0, 6.0) == pytest.approx(1.94)
    assert net_decimal_odds(1.0, 6.0) == pytest.approx(1.0)


def test_two_way_arbitrage_equalizes_payout_and_stake_total():
    candidate = Candidate(
        sport="Test",
        event="Even odds",
        market="Winner",
        outcomes=[
            Outcome(selection="A", bookmaker="Book 1", odds=2.1),
            Outcome(selection="B", bookmaker="Book 2", odds=2.1),
        ],
        total_stake=500,
    )
    result = calculate(candidate)

    assert result["roi"] == pytest.approx(0.05)
    assert result["stakes"] == [250.0, 250.0]
    assert sum(result["stakes"]) == pytest.approx(candidate.total_stake)
    assert result["equalised_payout"] == pytest.approx(525)
    assert result["profit"] == pytest.approx(25)


def test_three_outcome_rounding_preserves_total_stake():
    candidate = Candidate(
        sport="Soccer",
        event="Three outcomes",
        market="1X2",
        outcomes=[
            Outcome(selection="Home", bookmaker="A", odds=3.0),
            Outcome(selection="Draw", bookmaker="B", odds=3.0),
            Outcome(selection="Away", bookmaker="C", odds=3.0),
        ],
        total_stake=500,
    )
    result = calculate(candidate)

    assert result["roi"] == pytest.approx(0)
    assert sum(result["stakes"]) == pytest.approx(500)
    assert result["stakes"][-1] == round(500 - sum(result["stakes"][:-1]), 2)


def test_two_leg_position_roi_includes_betfair_net_return():
    legs = [
        SimpleNamespace(account_id=1, outcome_id="home", odds=2.1, stake=100),
        SimpleNamespace(account_id=2, outcome_id="away", odds=2.1, stake=100),
    ]
    result = calculate_position_roi(
        legs,
        ["home", "away"],
        {1: 0.0, 2: 6.0},
    )

    assert result["total_stake"] == 200
    assert result["scenarios"][0]["total_return"] == pytest.approx(210)
    assert result["scenarios"][1]["total_return"] == pytest.approx(203.4)
    assert result["expected_profit"] == pytest.approx(3.4)
    assert result["roi"] == pytest.approx(0.017)


def test_calculator_reports_negative_arbitrage_instead_of_hiding_loss():
    candidate = Candidate(
        sport="Test",
        event="No arb",
        market="Winner",
        outcomes=[
            Outcome(selection="A", bookmaker="Book 1", odds=1.8),
            Outcome(selection="B", bookmaker="Book 2", odds=1.8),
        ],
        total_stake=100,
    )
    result = calculate(candidate)

    assert result["roi"] < 0
    assert result["profit"] < 0
