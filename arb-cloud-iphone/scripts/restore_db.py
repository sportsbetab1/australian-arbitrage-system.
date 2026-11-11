import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import uuid

from scripts.backup_db import create_backup


def restore_backup(backup_path, db_path):
    source = Path(backup_path).resolve()
    target = Path(db_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Backup file not found: {source}")
    if source == target:
        raise ValueError("The backup and database paths must be different.")
    with sqlite3.connect(source) as check:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise sqlite3.DatabaseError("Source backup failed its integrity check.")

    target.parent.mkdir(parents=True, exist_ok=True)
    safety_copy = None
    if target.exists():
        safety_copy = create_backup(
            target,
            target.parent / "pre-restore",
            keep=None,
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    temporary = target.with_name(f"{target.name}.{stamp}.{uuid.uuid4().hex[:8]}.restore.tmp")
    try:
        with sqlite3.connect(source) as src, sqlite3.connect(temporary) as dst:
            src.backup(dst)
        with sqlite3.connect(temporary) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Restored database failed integrity check.")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {"database": target, "safety_backup": safety_copy}


def main():
    parser = argparse.ArgumentParser(
        description="Restore a verified SQLite backup; stop the app first."
    )
    parser.add_argument("backup_file")
    parser.add_argument("--db", default=os.getenv("DB_PATH", "./data/arbitrage.db"))
    args = parser.parse_args()
    result = restore_backup(args.backup_file, args.db)
    print(f"Restored {result['database']}")
    if result["safety_backup"]:
        print(f"Pre-restore safety copy: {result['safety_backup']}")


if __name__ == "__main__":
    main()
