from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from app import database, rate_limits


@pytest.fixture
def budgets(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "budgets.sqlite"))
    rate_limits.initialise()


def test_concurrent_workers_share_one_budget(budgets):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: rate_limits.consume("auth", "client", 5, timestamp=100), range(16)))
    assert results.count(0) == 5
    assert set(results) == {0, 20}


def test_budget_resets_and_expired_rows_are_removed(budgets):
    assert rate_limits.consume("auth", "client", 1, timestamp=119.5) == 0
    assert rate_limits.consume("auth", "client", 1, timestamp=119.5) == 1
    assert rate_limits.consume("auth", "client", 1, timestamp=120) == 0
    with closing(database.get_db()) as con:
        assert con.execute("SELECT COUNT(*) FROM request_budgets").fetchone()[0] == 1


def test_identity_and_scope_are_isolated_and_identity_not_stored(budgets):
    assert rate_limits.consume("auth", "192.0.2.1", 1, timestamp=100) == 0
    assert rate_limits.consume("auth", "192.0.2.2", 1, timestamp=100) == 0
    assert rate_limits.consume("odds", "192.0.2.1", 1, timestamp=100) == 0
    with closing(database.get_db()) as con:
        assert all("192.0.2." not in row[0] for row in con.execute("SELECT bucket FROM request_budgets"))
