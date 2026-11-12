from contextlib import closing
import json
import sqlite3

from argon2 import PasswordHasher
import pytest

from app import database
from scripts.manage_auth import manage_user, main


@pytest.fixture
def recovery_db(tmp_path, monkeypatch):
    path = tmp_path / "recovery.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    with closing(database.get_db()) as con, con:
        for username in ("hunta", "jay"):
            user_id = con.execute("INSERT INTO auth_users(username, display_name, password_hash, failed_attempts, locked_until) VALUES (?, ?, ?, 5, '2099-01-01T00:00:00+00:00')",
                                  (username, username.title(), PasswordHasher().hash("Original-test-passphrase"))).lastrowid
            for index in range(2):
                con.execute("INSERT INTO auth_sessions(token_hash, user_id, expires_at) VALUES (?, ?, '2099-01-01T00:00:00+00:00')",
                            (f"{username}-{index}", user_id))
    return path


def test_password_reset_revokes_only_target_sessions_and_audits(recovery_db):
    password = "Replacement-test-passphrase"
    result = manage_user(recovery_db, "hunta", "Verified administrator recovery", new_password=password)
    assert result == {"username": "hunta", "sessions_revoked": 2, "is_active": True}
    with closing(database.get_db()) as con:
        row = con.execute("SELECT password_hash, failed_attempts, locked_until FROM auth_users WHERE username = 'hunta'").fetchone()
        assert PasswordHasher().verify(row["password_hash"], password)
        assert (row["failed_attempts"], row["locked_until"]) == (0, None)
        assert [r[0] for r in con.execute("SELECT token_hash FROM auth_sessions ORDER BY token_hash")] == ["jay-0", "jay-1"]
        audit = con.execute("SELECT action, details FROM audit_log").fetchone()
        assert audit["action"] == "auth_password_reset"
        assert json.loads(audit["details"])["reason"] == "Verified administrator recovery"
        assert password not in audit["details"]
        assert row["password_hash"] not in audit["details"]


def test_revoke_sessions_preserves_password_and_lockout(recovery_db):
    with closing(database.get_db()) as con:
        before = tuple(con.execute("SELECT password_hash, failed_attempts, locked_until FROM auth_users WHERE username = 'jay'").fetchone())
    assert manage_user(recovery_db, "jay", "Lost device")["sessions_revoked"] == 2
    with closing(database.get_db()) as con:
        after = tuple(con.execute("SELECT password_hash, failed_attempts, locked_until FROM auth_users WHERE username = 'jay'").fetchone())
        assert before == after
        assert con.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 2


def test_reset_does_not_reactivate_disabled_account(recovery_db):
    with closing(database.get_db()) as con, con:
        con.execute("UPDATE auth_users SET is_active = 0 WHERE username = 'hunta'")
    assert manage_user(recovery_db, "hunta", "Recovery", new_password="Replacement-test-passphrase")["is_active"] is False


def test_audit_failure_rolls_back_password_and_session_changes(recovery_db):
    with closing(database.get_db()) as con, con:
        before = con.execute("SELECT password_hash FROM auth_users WHERE username = 'hunta'").fetchone()[0]
        con.execute("CREATE TRIGGER reject_recovery BEFORE INSERT ON audit_log BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        manage_user(recovery_db, "hunta", "Recovery", new_password="Replacement-test-passphrase")
    with closing(database.get_db()) as con:
        assert con.execute("SELECT password_hash FROM auth_users WHERE username = 'hunta'").fetchone()[0] == before
        assert con.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 4


@pytest.mark.parametrize("username,reason,password", [("other", "Recovery", None), ("hunta", " ", None), ("hunta", "Recovery", "short")])
def test_invalid_recovery_does_not_change_database(recovery_db, username, reason, password):
    with pytest.raises(ValueError):
        manage_user(recovery_db, username, reason, new_password=password)
    with closing(database.get_db()) as con:
        assert con.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 4
        assert con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "typo.sqlite"
    with pytest.raises(FileNotFoundError):
        manage_user(path, "hunta", "Recovery")
    assert not path.exists()


def test_cli_refuses_noninteractive_password_input(recovery_db, monkeypatch, capsys):
    from scripts import manage_auth
    monkeypatch.setattr(manage_auth.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        main(["reset-password", "--db", str(recovery_db), "--user", "hunta", "--reason", "Recovery"])
    assert error.value.code == 1
    assert "interactive terminal" in capsys.readouterr().err


def test_cli_revoke_reports_result_without_secrets(recovery_db, capsys):
    assert main(["revoke-sessions", "--db", str(recovery_db), "--user", "hunta", "--reason", "Lost device"]) == 0
    output = capsys.readouterr().out
    assert "revoked 2 session(s)" in output
    assert "Original-test-passphrase" not in output


def test_cli_reset_reads_hidden_input_and_reports_no_password(recovery_db, monkeypatch, capsys):
    from scripts import manage_auth
    password = "Terminal-test-passphrase"
    monkeypatch.setattr(manage_auth.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(manage_auth.getpass, "getpass", lambda prompt: password)
    assert main(["reset-password", "--db", str(recovery_db), "--user", "hunta", "--reason", "Recovery"]) == 0
    assert password not in capsys.readouterr().out
    with closing(database.get_db()) as con:
        assert PasswordHasher().verify(con.execute("SELECT password_hash FROM auth_users WHERE username = 'hunta'").fetchone()[0], password)


@pytest.mark.parametrize("failure", ["mismatch", "no_hidden_input"])
def test_cli_password_prompt_failure_preserves_sessions(recovery_db, monkeypatch, capsys, failure):
    from scripts import manage_auth
    import warnings
    monkeypatch.setattr(manage_auth.sys.stdin, "isatty", lambda: True)
    answers = iter(["Terminal-test-passphrase", "Different-test-passphrase"])
    def read_password(prompt):
        if failure == "no_hidden_input":
            warnings.warn("Cannot hide password input", manage_auth.getpass.GetPassWarning)
        return next(answers)
    monkeypatch.setattr(manage_auth.getpass, "getpass", read_password)
    with pytest.raises(SystemExit) as error:
        main(["reset-password", "--db", str(recovery_db), "--user", "hunta", "--reason", "Recovery"])
    assert error.value.code == 1
    assert "Terminal-test-passphrase" not in capsys.readouterr().err
    with closing(database.get_db()) as con:
        assert con.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 4
