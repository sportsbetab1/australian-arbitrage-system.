from contextlib import closing
import os
import sqlite3
import stat

import pytest

from app import database


@pytest.fixture
def storage(tmp_path, monkeypatch):
    path = tmp_path / "private.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    return path


def test_new_database_is_private_and_existing_permissions_are_preserved(storage):
    if os.name != "posix":
        pytest.skip("Windows uses ACLs")
    assert stat.S_IMODE(storage.stat().st_mode) == 0o600
    storage.chmod(0o640)
    with closing(database.get_db()) as con:
        assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 14
    assert stat.S_IMODE(storage.stat().st_mode) == 0o640


@pytest.mark.parametrize("table,insert", [
    ("audit_log", "INSERT INTO audit_log(action, details) VALUES ('test', 'original')"),
    ("profit_allocations", "INSERT INTO profit_allocations(realised_profit, reinvested_amount, withdrawal_amount) VALUES (10, 7.5, 2.5)"),
])
def test_history_can_be_appended_but_not_rewritten(storage, table, insert):
    database.init_db()  # Repeated startup preserves protections and history.
    with closing(database.get_db()) as con, con:
        con.execute(insert)
    for operation in (f"DELETE FROM {table}", f"UPDATE {table} SET id = 99",
                      f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}"):
        with closing(database.get_db()) as con, con:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute(operation)
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
