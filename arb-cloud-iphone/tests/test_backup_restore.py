import sqlite3
from contextlib import closing

import pytest

from scripts.backup_db import create_backup
from scripts.restore_db import restore_backup


def test_backup_and_restore_close_connections_before_replace(tmp_path, monkeypatch):
    from scripts import backup_db, restore_db

    database = tmp_path / "arbitrage.sqlite"
    make_db(database, "original")
    connections = []
    real_connect = sqlite3.connect
    real_replace = backup_db.os.replace

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def replace(source, target):
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
        return real_replace(source, target)

    monkeypatch.setattr(backup_db.sqlite3, "connect", connect)
    monkeypatch.setattr(backup_db.os, "replace", replace)
    backup = create_backup(database)
    restore_db.restore_backup(backup, database)


def test_failed_restore_replace_preserves_target_and_cleans_temporary(tmp_path, monkeypatch):
    from scripts import restore_db

    database = tmp_path / "arbitrage.sqlite"
    make_db(database, "before")
    backup = create_backup(database)
    make_db(database, "after")
    real_replace = restore_db.os.replace

    def fail_restore(source, target):
        if str(source).endswith(".restore.tmp"):
            raise PermissionError("simulated locked destination")
        return real_replace(source, target)

    monkeypatch.setattr(restore_db.os, "replace", fail_restore)
    with pytest.raises(PermissionError):
        restore_backup(backup, database)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "after"
    assert not list(tmp_path.glob("*.restore.tmp"))
    assert len(list((tmp_path / "pre-restore").glob("*.sqlite"))) == 1


def make_db(path, value):
    with closing(sqlite3.connect(path)) as con:
        with con:
            con.execute("CREATE TABLE IF NOT EXISTS sample(value TEXT)")
            con.execute("DELETE FROM sample")
            con.execute("INSERT INTO sample(value) VALUES (?)", (value,))


def test_backup_and_restore_preserve_a_safety_copy(tmp_path):
    database = tmp_path / "arbitrage.sqlite"
    backups = tmp_path / "backups"
    make_db(database, "before")
    backup = create_backup(database, backups)

    make_db(database, "after")
    result = restore_backup(backup, database)

    with closing(sqlite3.connect(database)) as con:
        assert con.execute("SELECT value FROM sample").fetchone()[0] == "before"
    assert result["safety_backup"] is not None
    assert result["safety_backup"].is_file()
    with closing(sqlite3.connect(result["safety_backup"])) as con:
        assert con.execute("SELECT value FROM sample").fetchone()[0] == "after"


def test_restore_rejects_corrupt_backup_without_replacing_database(tmp_path):
    database = tmp_path / "arbitrage.sqlite"
    corrupt = tmp_path / "bad.sqlite"
    make_db(database, "keep")
    corrupt.write_bytes(b"not a sqlite database")

    try:
        restore_backup(corrupt, database)
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("Corrupt backup should be rejected.")

    with closing(sqlite3.connect(database)) as con:
        assert con.execute("SELECT value FROM sample").fetchone()[0] == "keep"


def test_backup_retention_keeps_requested_count_and_rejects_zero(tmp_path):
    database = tmp_path / "arbitrage.sqlite"
    backups = tmp_path / "backups"
    make_db(database, "retention")
    created = [
        create_backup(database, backups, keep=2)
        for _ in range(3)
    ]
    assert len(set(created)) == 3
    assert len(list(backups.glob("arbitrage-*.sqlite"))) == 2

    try:
        create_backup(database, backups, keep=0)
    except ValueError as error:
        assert "At least one backup" in str(error)
    else:
        raise AssertionError("A zero-backup retention policy should be rejected.")
    assert len(list(backups.glob("arbitrage-*.sqlite"))) == 2

