from contextlib import closing
import json
import os
import sqlite3
import time

from app import database, main
from scripts.backup_db import create_backup
from scripts.preflight import inspect_host


def test_preflight_does_not_create_missing_database_or_expose_secrets(tmp_path):
    path = tmp_path / "missing.sqlite"
    secret = "private-sentinel-do-not-print"
    result = inspect_host(path, environ={"AUTH_SETUP_TOKEN": secret, "ODDS_API_KEY": secret})
    assert not result["ok"]
    assert not path.exists()
    assert secret not in json.dumps(result)


def test_preflight_checks_accounts_database_and_snapshot_age(tmp_path, monkeypatch):
    path = tmp_path / "application.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    main.initialise_system()
    with closing(database.get_db()) as con, con:
        con.executemany("INSERT INTO auth_users(username, display_name, password_hash) VALUES (?, ?, ?)",
                        [("hunta", "Hunta", "test-only"), ("jay", "Jay", "test-only")])
    backup = create_backup(path)
    result = inspect_host(path, environ={})
    assert result["ok"]
    assert any(c["name"] == "external_operations" and c["state"] == "warn" for c in result["checks"])
    old = time.time() - 27 * 3600
    os.utime(backup, (old, old))
    assert not inspect_host(path, environ={})["ok"]
    assert not inspect_host(path, environ={"AUTH_COOKIE_SECURE": "false"})["ok"]


def test_preflight_rejects_corrupt_latest_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "application.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    main.initialise_system()
    backup = create_backup(path)
    backup.write_bytes(b"invalid sqlite")
    checks = inspect_host(path, environ={})["checks"]
    assert any(c["name"] == "backup" and c["state"] == "fail" for c in checks)


def test_preflight_rejects_unrelated_but_valid_sqlite_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "application.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    main.initialise_system()
    backup = create_backup(path)
    backup.unlink()
    with closing(sqlite3.connect(backup)) as con, con:
        con.execute("CREATE TABLE unrelated(value TEXT)")
    assert any(c["name"] == "backup" and c["state"] == "fail" for c in inspect_host(path, environ={})["checks"])


def test_preflight_requires_expected_owners_not_just_two_accounts(tmp_path, monkeypatch):
    path = tmp_path / "application.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    main.initialise_system()
    with closing(database.get_db()) as con, con:
        con.executemany("INSERT INTO auth_users(username, display_name, password_hash) VALUES (?, ?, ?)",
                        [("other", "Other", "test-only"), ("unexpected", "Unexpected", "test-only")])
    assert any(c["name"] == "accounts" and c["state"] == "fail" for c in inspect_host(path, environ={})["checks"])


def test_preflight_allows_hunta_with_jay_pending(tmp_path, monkeypatch):
    path = tmp_path / "application.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    main.initialise_system()
    with closing(database.get_db()) as con, con:
        con.execute("INSERT INTO auth_users(username, display_name, password_hash) VALUES (?, ?, ?)",
                    ("hunta", "Hunta", "test-only"))
    checks = inspect_host(path, environ={})["checks"]
    assert any(c["name"] == "accounts" and c["state"] == "pass" for c in checks)
    assert any(c["name"] == "jay_login" and c["state"] == "warn" for c in checks)


def test_preflight_reports_nonprivate_existing_database(tmp_path, monkeypatch):
    import pytest
    if os.name != "posix":
        pytest.skip("Windows uses ACLs")
    path = tmp_path / "application.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    main.initialise_system()
    path.chmod(0o644)
    assert any(c["name"] == "database_permissions" and c["state"] == "fail" for c in inspect_host(path, environ={})["checks"])
