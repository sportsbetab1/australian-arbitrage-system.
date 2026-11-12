"""Verify a backup by restoring it into a disposable, isolated directory."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from scripts.restore_db import restore_backup


def fingerprint(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok" or con.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.DatabaseError("Database integrity or foreign-key check failed.")
        tables = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        counts = {}
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            counts[name] = con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        digest = hashlib.sha256()
        for line in con.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
    return {"tables": counts, "content_sha256": digest.hexdigest()}


def run_drill(backup):
    source = Path(backup).resolve()
    if not source.is_file():
        raise FileNotFoundError("Backup file not found.")
    before = fingerprint(source)
    with tempfile.TemporaryDirectory(prefix="arbitrage-recovery-") as directory:
        target = Path(directory) / "restored.sqlite"
        restore_backup(source, target)
        restored = fingerprint(target)
        if before != restored or before != fingerprint(source):
            raise sqlite3.DatabaseError("Restore verification did not match the backup.")
        # Exercise safety-copy creation and restoration over an existing database.
        result = restore_backup(source, target)
        if fingerprint(result["safety_backup"]) != before or fingerprint(target) != before:
            raise sqlite3.DatabaseError("Pre-restore safety copy verification failed.")
    return {"ok": True, "integrity": "ok", "safety_copy_verified": True, **before}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_file")
    args = parser.parse_args()
    print(json.dumps(run_drill(args.backup_file), sort_keys=True))


if __name__ == "__main__":
    main()
