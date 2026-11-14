"""Safe unit tests for the trial ledger; no network or real database."""
from datetime import datetime, timezone, timedelta
import pytest
from scripts.trial_credit_ledger import LIMIT, DURATION, RESERVATION, reserve


def test_fixed_trial_limits():
    assert LIMIT == 350
    assert DURATION == timedelta(hours=24)
    assert RESERVATION >= 1


def test_invalid_competition_rejected_without_database_access():
    with pytest.raises(ValueError):
        reserve(None, "../invalid")
