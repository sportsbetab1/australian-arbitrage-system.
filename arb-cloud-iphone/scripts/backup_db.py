import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import uuid


def _integrity_ok(path: Path) -> bool:
    with closing(sqlite3.connect(path)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return result == "ok"


def create_backup(db_path, backup_dir=None, keep=14):
    if keep is not None and int(keep) < 1:
        raise ValueError("At least one backup must be retained.")
    source = Path(db_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Database file not found: {source}")
    target_dir = Path(backup_dir).resolve() if backup_dir else source.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"arbitrage-{stamp}-{uuid.uuid4().hex[:8]}.sqlite"
    temporary = target.with_suffix(".sqlite.tmp")

    try:
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst)
        if not _integrity_ok(temporary):
            raise sqlite3.DatabaseError("Backup integrity check failed.")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    if keep is not None:
        backups = sorted(
            target_dir.glob("arbitrage-*.sqlite"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in backups[max(int(keep), 0):]:
            old.unlink()

    return target


def main():
    parser = argparse.ArgumentParser(description="Create a verified SQLite backup.")
    parser.add_argument("--db", default=os.getenv("DB_PATH", "./data/arbitrage.db"))
    parser.add_argument("--backup-dir", default=os.getenv("BACKUP_DIR"))
    parser.add_argument("--keep", type=int, default=int(os.getenv("BACKUP_KEEP", "14")))
    args = parser.parse_args()
    print(create_backup(args.db, args.backup_dir, args.keep))


if __name__ == "__main__":
    main()

