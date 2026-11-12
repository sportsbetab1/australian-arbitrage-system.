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


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_restore_refuses_existing_journals_without_touching_target(tmp_path, suffix):
    database = tmp_path / "live.sqlite"
    make_db(database, "backup")
    backup = create_backup(database)
    make_db(database, "current")
    journal = tmp_path / (database.name + suffix)
    journal.write_bytes(b"preserve recovery data")
    before = database.read_bytes()
    with pytest.raises(ValueError, match="Stop all database writers"):
        restore_backup(backup, database)
    assert database.read_bytes() == before
    assert journal.read_bytes() == b"preserve recovery data"
    assert not (tmp_path / "pre-restore").exists()


def test_backup_captures_committed_wal_data_and_restore_refuses_live_wal(tmp_path):
    database = tmp_path / "wal.sqlite"
    with closing(sqlite3.connect(database)) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE sample(value TEXT)")
        con.execute("INSERT INTO sample VALUES ('committed in WAL')")
        con.commit()
        backup = create_backup(database)
        with closing(sqlite3.connect(backup)) as snapshot:
            assert snapshot.execute("SELECT value FROM sample").fetchone()[0] == "committed in WAL"
        with pytest.raises(ValueError, match="journal files"):
            restore_backup(backup, database)
    restore_backup(backup, database)


def test_backup_and_restore_files_are_private_on_posix(tmp_path):
    import os
    import stat
    if os.name != "posix":
        pytest.skip("Windows uses ACLs rather than POSIX mode bits")
    database = tmp_path / "private.sqlite"
    make_db(database, "private")
    backup = create_backup(database)
    restored = tmp_path / "restored.sqlite"
    restore_backup(backup, restored)
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(restored.stat().st_mode) == 0o600


def test_drill_restores_full_application_schema_and_preserves_source(tmp_path, monkeypatch):
    from app import database
    from scripts.recovery_drill import run_drill
    path = tmp_path / "application.sqlite"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    database.migrate_position_schema()
    backup = create_backup(path)
    before = backup.read_bytes()
    result = run_drill(backup)
    assert result["ok"] and result["safety_copy_verified"]
    assert result["tables"]["accounts"] == 16
    assert "position_legs" in result["tables"]
    assert backup.read_bytes() == before


def test_drill_rejects_foreign_key_violations(tmp_path):
    from scripts.recovery_drill import run_drill
    path = tmp_path / "invalid.sqlite"
    with closing(sqlite3.connect(path)) as con, con:
        con.executescript("CREATE TABLE parent(id INTEGER PRIMARY KEY); "
                          "CREATE TABLE child(parent_id INTEGER REFERENCES parent(id)); "
                          "INSERT INTO child VALUES (999);")
    with pytest.raises(sqlite3.DatabaseError, match="foreign-key"):
        run_drill(path)
