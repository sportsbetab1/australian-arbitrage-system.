import sqlite3

from scripts.backup_db import create_backup
from scripts.restore_db import restore_backup


def make_db(path, value):
    with sqlite3.connect(path) as con:
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

    with sqlite3.connect(database) as con:
        assert con.execute("SELECT value FROM sample").fetchone()[0] == "before"
    assert result["safety_backup"] is not None
    assert result["safety_backup"].is_file()
    with sqlite3.connect(result["safety_backup"]) as con:
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

    with sqlite3.connect(database) as con:
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
